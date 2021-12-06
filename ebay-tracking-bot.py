#!/usr/bin/python3
import sys, time, re, os, json, subprocess, pytz, schedule, hashlib, logging, icu, traceback, telepot
from telepot.loop import MessageLoop
from telepot.namedtuple import InlineKeyboardMarkup, InlineKeyboardButton
from telepot.namedtuple import ReplyKeyboardRemove
from datetime import datetime, timedelta, MAXYEAR
from google_trans_new import google_translator
from urllib.parse import urlparse
from urllib.request import urlopen
from lxml import etree
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from contextlib import suppress

# If modifying these scopes, delete the file token.json.
GOOGLE_API_SCOPES = ['https://www.googleapis.com/auth/calendar',
                     'https://www.googleapis.com/auth/calendar.events']

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

def readDB():
    log.debug("Reading DB")
    with open('ebay-tracking-bot_data.json', 'r', encoding='utf-8') as data_file:
        data = json.load(data_file)
    
    log.debug("Done reading DB")
    return data

def urlValidator(x):
    try:
        result = urlparse(x)
        return all([result.scheme, result.netloc])
    except:
        return False

def createItemDescription(item):
    desc = "%s\n\nPrezzo: <b>%0.2f</b>, Sped.: %0.2f" % (item['title'], item['cur_price'], item['shipping_cost'])
    
    if item['num_bids']:
        desc += ", Offerte: %d" % item['num_bids']

    if item['end_date']:
        desc += "\nScadenza: *%s*" % item['end_date'].strftime('%d/%m/%Y %H:%M:%S')
    
    if item['notes']:
        desc += "\nNote: %s" % item['notes']
        
    desc += "\nURL: %s" % item['url']
    return desc
    
def checkEbayItems():
    now = datetime.now(TZ_CET)
    log.debug("Checking items...")
    
    events = listCalendarEvents()
    for event in events:
        with suppress(KeyError):
            endDate = datetime.fromisoformat(event['end']['dateTime'])
            lastCrawled = datetime.fromisoformat(event['extendedProperties']['private']['last_crawled'])
            delta = endDate - now
            if delta.days >= 0:
                if now - lastCrawled > EBAY_ITEM_CRAWLING_INTERVAL or delta < EBAY_ITEM_ALERT_DELTA:
                    crawledItem = crawlEbayItem(event['source']['url'])
                    desc = createItemDescription(crawledItem)
                    if desc != event['description']:
                        updateCalendarEvent(item)
                    if delta < EBAY_ITEM_ALERT_DELTA:
                        bot.sendMessage(data['telegram_chat_id'], "Oggetto in scadenza fra %d minuti!\n%s" % ((delta.seconds-30) / 60 + 1, desc), parse_mode="html")
    #            else:
    #                break # Items are sorted by end_date, so no reason to continue

    log.debug("Done checking items...")

def crawlEbayItem(url):
    if not hasattr(crawlEbayItem, 'htmlparser'):
        crawlEbayItem.htmlparser = etree.HTMLParser()  # static local variable
    
    log.debug("Crawling url: %s" % url)
    
    newItem = {'url': url}
    
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
        raise "Oggetto senza scadenza, non tracciabile"
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

def printEbayURLs(chat_id):
    log.debug("Printing URLs")
    events = listCalendarEvents()
    if events:
        msg = ''
        for event in events:
            with suppress(KeyError):
                msg += event['source']['url'] + '\n'
        bot.sendMessage(chat_id, msg[:-1])
    else:
        bot.sendMessage(chat_id, "Non ci sono oggetti Ebay tracciati")
    log.debug("Done listing")
        
def listEbayItems(chat_id):
    log.debug("Listing items")
    events = listCalendarEvents()
    if events:
        i=1
        for event in events:
            bot.sendMessage(chat_id, str(i) + ') ' + event['description'], parse_mode="html")
            i += 1
    else:
        bot.sendMessage(chat_id, "Non ci sono oggetti Ebay tracciati")
    log.debug("Done listing")

def getUrlMd5(url):
    return hashlib.md5(url.encode('utf-8')).hexdigest()
    
def createEvent(item):
    event = {
      'summary': 'Asta Ebay',
      'description': createItemDescription(item),
      'start': {
        'dateTime': item['end_date'].isoformat(),
        'timeZone': 'Europe/Rome',
      },
      'end': {
        'dateTime': item['end_date'].isoformat(),
        'timeZone': 'Europe/Rome',
      },
      'reminders': {
        'useDefault': False,
        'overrides': [
          {'method': 'popup', 'minutes': 10},
        ],
      },
      'source': {
        'url': item['url'], 
      },
      'extendedProperties': {
        'private': {
          'item_url_md5': getUrlMd5(item['url']),
          'last_crawled': datetime.now(TZ_CET).isoformat(),
        },
      },
    }
    return event

def listCalendarEvents():
    events_result = google_cal_service.events().list(calendarId=data['calendar_id'], timeMin=datetime.now(TZ_CET).isoformat(), maxResults=100, singleEvents=True, orderBy='startTime').execute()
    events = events_result.get('items', [])
    return events

def isCalendarEventPresent(url):
    events_result = google_cal_service.events().list(calendarId=data['calendar_id'], maxResults=1, privateExtendedProperty="item_url_md5="+getUrlMd5(url), singleEvents=True).execute()
    events = events_result.get('items', [])
    return len(events) > 0
    
def addCalendarEvent(item):
    event = createEvent(item)
    event = google_cal_service.events().insert(calendarId=data['calendar_id'], body=event).execute()

def updateCalendarEvent(item):
    events_result = google_cal_service.events().list(calendarId=data['calendar_id'], maxResults=1, privateExtendedProperty="item_url_md5="+getUrlMd5(item['url']), singleEvents=True).execute()
    events = events_result.get('items', [])

    if events:
        event = createEvent(item)
        event = google_cal_service.events().update(calendarId=data['calendar_id'], eventId=events[0]['id'], body=event).execute()

def deleteCalendarEvent(urlMd5):
    events_result = google_cal_service.events().list(calendarId=data['calendar_id'], maxResults=1, privateExtendedProperty="item_url_md5="+urlMd5, singleEvents=True).execute()
    events = events_result.get('items', [])

    if events:
        google_cal_service.events().delete(calendarId=data['calendar_id'], eventId=events[0]['id']).execute()

def trackEbayItem(chat_id, urls):
    urls = urls.split('\n')
    for url in urls:
        if urlValidator(url):
            log.debug("Tracking new item: %s" % url)
            try:
                url = url.partition('?')[0]
                if isCalendarEventPresent(url):
                    bot.sendMessage(chat_id, "L'oggetto era già tracciato!")
                else:
                    item = crawlEbayItem(url)
                    addCalendarEvent(item)
                    bot.sendMessage(chat_id, "Oggetto tracciato correttamente")
            except IndexError:
                bot.sendMessage(chat_id, "Impossibile leggere i dati dalla pagina Ebay")
                log.error(traceback.format_exc())
            except HttpError:
                bot.sendMessage(chat_id, "Errore durante l'aggiunta dell'evento al calendario")
                log.error(traceback.format_exc())
                
            log.debug("Done tracking")
        else:
            bot.sendMessage(chat_id, "URL non valido: " + url)

def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]
        
def removeEbayItem(chat_id):
    events = listCalendarEvents()
    if events:
        log.debug("Listing items to remove")
        buttons = []
        i=1
        for event in events:
            with suppress(KeyError):
                buttons.append(InlineKeyboardButton(text=str(i), callback_data=getUrlMd5(event['source']['url'])))
            i += 1
    
        bot.sendMessage(chat_id, "Quali oggetti vuoi rimuovere?", reply_markup=InlineKeyboardMarkup(inline_keyboard=list(chunks(buttons, 8))))
        log.debug("Done listing items to remove")
    else:
        bot.sendMessage(chat_id, "Non ci sono oggetti Ebay tracciati")

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
            elif msg['text'] == '/printurls':
                printEbayURLs(chat_id)
            elif msg['text'] == '/remove':
                removeEbayItem(chat_id)
            else:
                trackEbayItem(chat_id, msg['text'])
        except:
            log.error(traceback.format_exc())
            pass
            
    log.debug("Done processing message")

def on_callback_query(msg):
    query_id, from_id, query_data = telepot.glance(msg, flavor='callback_query')
    log.debug("Received callback query. Query id: %s, from id: %s, query data: %s" % (query_id, from_id, query_data))

    try:
        deleteCalendarEvent(query_data)
        bot.answerCallbackQuery(query_id, text="Oggetto eliminato")
    except HttpError:
        bot.answerCallbackQuery(query_id, text="Oggetto non trovato!")

    log.debug("Done callback query")

script_dir = os.path.dirname(os.path.realpath(__file__))
os.chdir(script_dir)

log = setup_custom_logger('ebay-bot', logging.DEBUG)

log.info ("---------------------------------------------------")
log.info ("Starting bot...")

data = readDB()

translator = google_translator()

# Google Calendar API initialization
google_creds = None
# The file token.json stores the user's access and refresh tokens, and is
# created automatically when the authorization flow completes for the first
# time.
if os.path.exists('ebay-tracking-bot_google_token.json'):
    google_creds = Credentials.from_authorized_user_file('ebay-tracking-bot_google_token.json', GOOGLE_API_SCOPES)
# If there are no (valid) credentials available, let the user log in.
if not google_creds or not google_creds.valid:
    if google_creds and google_creds.expired and google_creds.refresh_token:
        google_creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(
            'ebay-tracking-bot_google_client_secret.json', SCOPES)
        google_creds = flow.run_local_server(port=0)
    # Save the credentials for the next run
    with open('ebay-tracking-bot_google_token.json', 'w') as token:
        token.write(google_creds.to_json())

google_cal_service = build('calendar', 'v3', credentials=google_creds)

# Bot initialization
bot = telepot.Bot(data['telegram_token'])
MessageLoop(bot, {'chat': on_chat_message, 'callback_query': on_callback_query}).run_as_thread()

log.info ("Bot started. Listening ...")

# Item check sheduling
schedule.every(2).minutes.do(checkEbayItems)

# Keep the program running.
while 1:
    schedule.run_pending()
    time.sleep(10)

