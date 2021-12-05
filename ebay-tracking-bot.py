#!/usr/bin/python3
import sys
import time
import telepot
from telepot.loop import MessageLoop
from telepot.namedtuple import InlineKeyboardMarkup, InlineKeyboardButton
from telepot.namedtuple import ReplyKeyboardRemove
import subprocess
import re, os, json
from datetime import datetime, timedelta, MAXYEAR
from google_trans_new import google_translator
import pytz
from enum import Enum
from urllib.parse import urlparse
from urllib.request import urlopen
from lxml import etree
import schedule
import hashlib
import logging
import icu
import traceback

TZ_CET = pytz.timezone('CET')
DATE_FORMATTER = icu.DateFormat.createDateTimeInstance(icu.DateFormat.MEDIUM,
                                           icu.DateFormat.MEDIUM,
                                           icu.Locale.getItalian())
                                           
EBAY_ITEM_ALERT_DELTA = timedelta(days=0, minutes=10)

EBAY_ITEM_CRAWLING_INTERVAL = timedelta(days=0, minutes=60)

def setup_custom_logger(name, level):
    formatter = logging.Formatter(fmt='%(asctime)s %(levelname)-8s %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S')
    screen_handler = logging.StreamHandler(stream=sys.stdout)
    screen_handler.setFormatter(formatter)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(screen_handler)
    return logger

def date_serializer(obj):
    if isinstance(obj, datetime):
        return { '_isoformat': obj.isoformat() }
    raise TypeError('...')

def date_parser(obj):
    _isoformat = obj.get('_isoformat')
    if _isoformat is not None:
        return datetime.fromisoformat(_isoformat)
    return obj

def readDB():
    log.debug("Reading DB")
    with open('ebay-tracking-bot_data.json', 'r', encoding='utf-8') as data_file:
        data = json.load(data_file, object_hook=date_parser)
    
    log.debug("Done reading DB")
    return data

def writeDB(data):
    log.debug("Writing DB")
    if not hasattr(crawlEbayItem, 'MAX_DATE'):
        crawlEbayItem.MAX_DATE = datetime(MAXYEAR, 12, 31, tzinfo=TZ_CET)  # static local variable
        
    if data['ebay_items']:
        data['ebay_items'].sort(key=lambda x: x['end_date'] or crawlEbayItem.MAX_DATE)
            
    with open('ebay-tracking-bot_data.json', 'w', encoding='utf-8') as data_file:
        json.dump(data, data_file, ensure_ascii=False, indent=4, default=date_serializer)
    
    log.debug("Done writing DB")
    return data

def urlValidator(x):
    try:
        result = urlparse(x)
        return all([result.scheme, result.netloc])
    except:
        return False

def sendItemMessage(chat_id, item, prefix):
    msg = "%s%s\n\nPrezzo: *%0.2f*, Sped.: %0.2f"
    
    if item['end_date']:
        now = datetime.now(TZ_CET)
        if item['end_date'] < now:
            prefix += "*SCADUTA!!!*\n"
        msg += ", Scadenza: *%s*" % item['end_date'].strftime('%d/%m/%Y %H:%M:%S')
        
    if item['num_bids']:
        msg += ", Offerte: %d" % item['num_bids']
    
    if item['notes']:
        msg += "\nNote: %s" % item['notes']
        
    msg += "\nURL: %s"
    msg = msg % (prefix,
        item['title'], 
        item['cur_price'], 
        item['shipping_cost'], 
        item['url'])
        
    bot.sendMessage(chat_id, msg, parse_mode="markdown")

def checkEbayItems():
    now = datetime.now(TZ_CET)
    log.debug("Checking items...")
    
    items = data['ebay_items']
    if items:
        for i, item in enumerate(items):
            if item['end_date']:
                delta = item['end_date'] - now
                if delta.days >= 0:
                    if delta < EBAY_ITEM_ALERT_DELTA:
                        item = crawlEbayItem(item['url'])
                        items[i] = item
                        sendItemMessage(data['telegram_chat_id'], item, "Oggetto in scadenza fra %d minuti!\n" % ((delta.seconds-30) / 60 + 1))
                    else:
                        break # Items are sorted by end_date, so no reason to continue

                    if not item['last_crawled'] or now - item['last_crawled'] > EBAY_ITEM_CRAWLING_INTERVAL:
                        item = crawlEbayItem(item['url'])
                        items[i] = item
    log.debug("Done checking items...")

def crawlEbayItem(url):
    if not hasattr(crawlEbayItem, 'htmlparser'):
        crawlEbayItem.htmlparser = etree.HTMLParser()  # static local variable
    
    newItem = {'url': url}

    log.debug("Crawling url: %s" % url)
    
    now = datetime.now(TZ_CET)
    
    newItem['last_crawled'] = now
    
    response = urlopen(url)
    tree = etree.parse(response, crawlEbayItem.htmlparser)

    newItem['title'] = tree.xpath("//h1[@id='itemTitle']/text()")[0]
    
    endDate = tree.xpath("//span[@class='vi-tm-left']/span/text()")
    if endDate:
        endDate = [n.replace('(','') for n in endDate]
        endDate = [n.replace(')','') for n in endDate]
        endDate = endDate[0]+' '+endDate[1].split(sep=' ')[0] #[6]
        ts = DATE_FORMATTER.parse(endDate)
        endDate = datetime.fromtimestamp(ts, TZ_CET)
        
        # This is only valid for current locale [locale.setlocale(locale.LC_TIME, ('it', 'UTF-8'))]
        # but the target locale must be installed on the system
        #endDate = datetime.strptime(endDate, '%d %b %Y %H:%M:%S').replace(tzinfo=TZ_CET) 
    else:
        endDate = None
    #print(endDate)
    
    newItem['end_date'] = endDate
    
    numBids = tree.xpath("//span[@id='qty-test']/text()")
    if numBids:
        newItem['num_bids'] = int(numBids[0])
    else:
        newItem['num_bids'] = None
    
    notes = tree.xpath("//div[@class='ux-labels-values__labels' and div/div/span/text() = 'Note del venditore:' ]/following-sibling::div/div/div/span/text()")
    if notes:
        notes = notes[0]
        try:
            notes = translator.translate(notes, lang_tgt='it')
        except:
            pass
    else:
        notes = None
    newItem['notes'] = notes
    
    currentPrice = tree.xpath("//span[@id='prcIsum_bidPrice']/text()")
    if not currentPrice:
        currentPrice = tree.xpath("//span[@id='prcIsum']/text()")
    if currentPrice:
        res = re.search(r'([0-9]*,[0-9]*)', currentPrice[0])
        currentPrice = float(res.groups(0)[0].replace('.','').replace(',','.'))
        newItem['cur_price'] = currentPrice
    else:
        raise "Impossibile ricavare il prezzo dell'oggetto"
        
    shippingCost = tree.xpath("//span[@id='fshippingCost']/span/text()")[0]
    try:
        if re.search(r'[0-9]',shippingCost):
            res = re.search(r'([0-9]*,[0-9]*)', shippingCost)
            shippingCost = float(res.groups(0)[0].replace('.','').replace(',','.'))
        else:
            shippingCost = 0
    except TypeError:
        shippingCost = 0
    newItem['shipping_cost'] = shippingCost

    log.debug("Done crawling")
    
    return newItem

def listEbayItems(chat_id):
    if data['ebay_items']:
        log.debug("Listing items")
        i=1
        for item in data['ebay_items']:
            sendItemMessage(chat_id, item, str(i) + ') ')
            i += 1
        log.debug("Done listing")
    else:
        bot.sendMessage(chat_id, "Non ci sono oggetti Ebay tracciati")

def trackEbayItem(chat_id, url):
    if urlValidator(url):
        log.debug("Tracking new item: %s" % url)
        try:
            url = url.partition('?')[0]
            if any(x['url'] == url for x in data['ebay_items']):
                bot.sendMessage(chat_id, "L'oggetto era già tracciato!")
            else:
                item = crawlEbayItem(url)
                data['ebay_items'].append(item)
                writeDB(data)
                bot.sendMessage(chat_id, "Oggetto tracciato correttamente")
        except IndexError:
            bot.sendMessage(chat_id, "Impossibile leggere i dati dalla pagina Ebay")
        log.debug("Done tracking")
    else:
        bot.sendMessage(chat_id, "URL non valido")

def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]
        
def removeEbayItem(chat_id):
    if data['ebay_items']:
        log.debug("Listing items to remove")
        buttons = []
        i=1
        for item in data['ebay_items']:
            buttons.append(InlineKeyboardButton(text=str(i), callback_data=hashlib.md5(item['url'].encode('utf-8')).hexdigest()))
            i += 1
    
        bot.sendMessage(chat_id, "Quali oggetti vuoi rimuovere?", reply_markup=InlineKeyboardMarkup(inline_keyboard=list(chunks(buttons, 8))))
        log.debug("Done listing items to remove")
    else:
        bot.sendMessage(chat_id, "Non ci sono oggetti Ebay tracciati")

def removeEbayExpiredItems(chat_id):
    now = datetime.now(TZ_CET)
    
    log.debug("Removing expired items: %s" % str(now))
    items = data['ebay_items']
    old_len = len(items)
    items[:] = [x for x in items if x['end_date'] is None or x['end_date'] > now]
    
    if len(items) < old_len:
        writeDB(data)
        bot.sendMessage(chat_id, "%d oggetti eliminati" % (old_len - len(items)))
    else:
        bot.sendMessage(chat_id, "Nessun oggetto eliminato")
        
    log.debug("Done removing expired items")
    
def on_chat_message(msg):
    global status
    content_type, chat_type, chat_id = telepot.glance(msg)
    
    if chat_id != data['telegram_chat_id']:
        log.warn("Discarded message because received by an unknown chat id: %s" % chat_id)
        return
        
    if content_type == 'text':
        log.debug("Received message. Content type : %s, chat type: %s, chat id: %s, msg: %s" % (content_type, chat_type, chat_id, msg['text']))

        try:
            if msg['text'] == '/list':
                listEbayItems(chat_id)
            elif msg['text'] == '/track':
                bot.sendMessage(chat_id, "Inserisci l'URL di un oggetto Ebay")
            elif msg['text'] == '/remove':
                removeEbayItem(chat_id)
            elif msg['text'] == '/remove_expired':
                removeEbayExpiredItems(chat_id)
            else:
                trackEbayItem(chat_id, msg['text'])
        except:
            log.error(traceback.format_exc())
            pass
            
    log.debug("Done processing message")

def on_callback_query(msg):
    query_id, from_id, query_data = telepot.glance(msg, flavor='callback_query')
    log.debug("Received callback query. Query id: %s, from id: %s, query data: %s" % (query_id, from_id, query_data))

    items = data['ebay_items']
    old_len = len(items)
    items[:] = [x for x in items if not hashlib.md5(x['url'].encode('utf-8')).hexdigest() == query_data]
    
    if len(items) < old_len:
        writeDB(data)
        bot.answerCallbackQuery(query_id, text="Oggetto eliminato")
    else:
        bot.answerCallbackQuery(query_id, text="Oggetto non trovato!")
    
    log.debug("Done callback query")

script_dir = os.path.dirname(os.path.realpath(__file__))
os.chdir(script_dir)

log = setup_custom_logger('ebay-bot', logging.INFO)

log.info ("---------------------------------------------------")
log.info ("Starting bot...")

data = readDB()

translator = google_translator()

bot = telepot.Bot(data['telegram_token'])
MessageLoop(bot, {'chat': on_chat_message, 'callback_query': on_callback_query}).run_as_thread()

log.info ("Bot started. Listening ...")

schedule.every(2).minutes.do(checkEbayItems)

# Keep the program running.
while 1:
    schedule.run_pending()
    time.sleep(10)

