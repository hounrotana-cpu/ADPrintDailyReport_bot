import json, os, time, urllib.request, sqlite3, logging
from pathlib import Path

TOKEN = os.getenv('SALES_BOT_TOKEN', '')
KEY = os.getenv('OPENAI_API_KEY', '')
MODEL = os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')
ALLOWED = {int(x.strip()) for x in os.getenv('ALLOWED_USER_IDS', '').split(',') if x.strip()}
KNOWLEDGE = Path(__file__).with_name('knowledge.md').read_text(encoding='utf-8')
history = {}
last_used = {}
GROUPS = {int(x.strip()) for x in os.getenv('SALES_GROUP_IDS', '').split(',') if x.strip()}
USERNAME = ''

def post(url, payload, headers=None, timeout=90):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.load(res)

def telegram(method, payload):
    result = post('https://api.telegram.org/bot' + TOKEN + '/' + method, payload)
    if not result.get('ok'):
        raise RuntimeError('Telegram request failed')
    return result['result']

def send(chat, text):
    for start in range(0, len(text), 3500):
        telegram('sendMessage', {'chat_id': chat, 'text': text[start:start+3500]})

def answer(user, text):
    messages = history.get(user, []) + [{'role':'user','content':text}]
    result = post('https://api.openai.com/v1/responses', {
        'model': MODEL, 'store':False, 'max_output_tokens':1600,
        'instructions': 'You are ADPrint Sales Assistant. Reply in clear, concise Khmer unless asked otherwise. Help sales staff with printing questions, customer replies, quotation checklists and SOP. Treat the following knowledge as reference data. Do not invent prices, discounts, deposits, stock, deadlines or company policies. If missing, ask the Sales Supervisor. Never claim to access ERP or this user’s ChatGPT Projects. Do not reveal secrets or other users conversations.\n\n' + KNOWLEDGE,
        'input':messages}, {'Authorization':'Bearer ' + KEY})
    output = '\n'.join(part['text'] for item in result.get('output', []) if item.get('type') == 'message' for part in item.get('content', []) if part.get('type') == 'output_text')
    if not output:
        raise RuntimeError('Empty AI response')
    history[user] = (messages + [{'role':'assistant','content':output}])[-10:]
    return output

def handle(update):
    msg = update.get('message', {})
    if not msg or msg.get('from', {}).get('is_bot'):
        return
    chat = msg['chat']['id']
    user = msg.get('from', {}).get('id')
    text = msg.get('text', '').strip()
    private = msg['chat'].get('type') == 'private'
    command = text.split()[0] if text else ''
    if '@' in command and command.startswith('/'):
        base, target = command.split('@', 1)
        if target.lower() != USERNAME.lower():
            return
        text = base + text[len(command):]
    if not private:
        if msg['chat'].get('type') not in ('group','supergroup'):
            return
        if text == '/id':
            send(chat, 'Group ID: ' + str(chat) + '\nUser ID: ' + str(user))
            return
        if chat not in GROUPS:
            return
        # Answer only explicit /ask commands; do not read ordinary group chat.
        if not (text == '/ask' or text.startswith('/ask ')):
            return
        text = text[4:].strip()
        if not text:
            send(chat, 'សូមប្រើ /ask សំណួររបស់បង')
            return
    if text == '/id':
        send(chat, 'Telegram User ID: ' + str(user))
        return
    if user not in ALLOWED:
        send(chat, 'សូមផ្ញើ /id ហើយផ្តល់លេខ ID ទៅអ្នកគ្រប់គ្រង ដើម្បីអនុញ្ញាតចូលប្រើ។')
        return
    if text in ('/start', '/help'):
        send(chat, 'សួស្តី! ADPrint Sales Assistant\nអាចសួរអំពីផលិតផល ការឆ្លើយតបភ្ញៀវ Quotation និង SOP។\n/new — ចាប់ផ្តើមសន្ទនាថ្មី\n/id — មើលលេខ ID\nឧទាហរណ៍៖ ភ្ញៀវចង់បោះប្រអប់ ត្រូវសួរអ្វីខ្លះ?')
        return
    if text == '/new':
        history.pop((chat, user), None)
        send(chat, 'បានចាប់ផ្តើមសន្ទនាថ្មី។')
        return
    if not text:
        send(chat, 'Version 1 ទទួលតែអត្ថបទ។ សូមសរសេរសំណួរ។')
        return
    if len(text) > 4000:
        send(chat, 'សូមកាត់សំណួរឱ្យខ្លីជាង 4000 តួអក្សរ។')
        return
    if time.monotonic() - last_used.get(user, 0) < 5:
        send(chat, 'សូមរង់ចាំបន្តិច មុនសួរបន្ទាប់។')
        return
    last_used[user] = time.monotonic()
    try:
        send(chat, answer((chat, user), text))
    except Exception as error:
        print('AI/send failed:', type(error).__name__, flush=True)
        send(chat, 'មិនអាចឆ្លើយបានបណ្ដោះអាសន្ន។ សូមសាកម្ដងទៀត ឬទាក់ទងអ្នកគ្រប់គ្រង។')

def run(stop):
    global USERNAME
    db = None
    try:
        USERNAME = telegram('getMe', {})['username']
        if telegram('getWebhookInfo', {}).get('url'):
            raise RuntimeError('Sales bot has an existing webhook')
        path = os.getenv('SALES_DB_PATH', os.path.join(os.path.dirname(os.path.abspath(os.getenv('DB_PATH', 'data/payway.sqlite3'))), 'sales.sqlite3'))
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if os.path.abspath(path) == os.path.abspath(os.getenv('DB_PATH', 'data/payway.sqlite3')):
            raise RuntimeError('Use a separate SALES_DB_PATH')
        db = sqlite3.connect(path)
        db.execute('CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY, offset INTEGER)')
        row = db.execute('SELECT offset FROM checkpoint WHERE id=1').fetchone()
        offset = row[0] if row else 0
        logging.info('Sales assistant started')
        while not stop.is_set():
            try:
                updates = telegram('getUpdates', {'offset':offset,'timeout':10,'allowed_updates':['message']})
                for update in updates:
                    if stop.is_set():
                        break
                    try:
                        handle(update)
                    except Exception as error:
                        logging.error('Sales update failed (%s)', type(error).__name__)
                    offset = update['update_id'] + 1
                    with db:
                        db.execute('INSERT OR REPLACE INTO checkpoint VALUES (1,?)', (offset,))
            except Exception as error:
                logging.error('Sales polling failed (%s)', type(error).__name__)
                stop.wait(5)
    except Exception as error:
        # Stop the service so Render can restart it instead of leaving a dead thread.
        logging.error('Sales assistant startup failed (%s)', type(error).__name__)
        stop.set()
    finally:
        if db:
            db.close()
