#!/usr/bin/python3
import sys
import time
import telepot
from telepot.loop import MessageLoop
import subprocess
import re, os, json
from datetime import datetime, timedelta, MAXYEAR
    
ipaddrCmd = ['/usr/bin/curl', '-sm', '3', 'icanhazip.com']

def readDB():
    with open('home-notification-bot_data.json', 'r', encoding='utf-8') as data_file:
        data = json.load(data_file)
    
    return data

def on_chat_message(msg):
    global status
    content_type, chat_type, chat_id = telepot.glance(msg)
    #print(content_type, chat_type, chat_id)

    if chat_id != data['telegram_chat_id']:
        return

    if content_type == 'text':
        if msg['text'] == '/ip_address':
            ipaddress = subprocess.check_output(ipaddrCmd)
            bot.sendMessage(chat_id, ipaddress)
        elif msg['text'] == '/chat_id':
            bot.sendMessage(chat_id, chat_id)
        else:
            bot.sendMessage(chat_id, "Comando non riconosciuto")
            
script_dir = os.path.dirname(os.path.realpath(__file__))
os.chdir(script_dir)

data = readDB()

bot = telepot.Bot(data['telegram_token'])
MessageLoop(bot, {'chat': on_chat_message}).run_as_thread()
print ("Listening ...")

# Keep the program running.
while 1:
    time.sleep(10)


