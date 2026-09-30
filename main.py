"""PayWay notification ledger. Python standard library only; run one instance."""
import os, re, json, sqlite3, signal, threading, logging, secrets
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen

TZ = ZoneInfo('Asia/Phnom_Penh')
STOP = threading.Event()
PATTERN = re.compile(r'^\s*(\$|USD\s*|KHR\s*|៛)\s*([\d,]+(?:\.\d{1,2})?)\s+paid by\s+.+?\s+on\s+([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{1,2}:\d{2}\s*[AP]M)\s+via\s+.+?\bTrx\.\s*ID:\s*(\d+),\s*APV:\s*(\d+)\.?\s*$', re.S)
MONTHS = {m:i+1 for i,m in enumerate('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split())}

def parse_payment(text, timestamp):
    m = PATTERN.fullmatch(text)
    if not m:
        return None
    currency, amount, month, day, clock, trx, apv = m.groups()
    if not re.fullmatch(r'(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?', amount):
        return None
    try:
        received = datetime.fromtimestamp(timestamp, TZ)
        t = datetime.strptime(clock.replace(' ', ''), '%I:%M%p')
        candidates = []
        for year in (received.year-1, received.year, received.year+1):
            try:
                candidates.append(datetime(year, MONTHS[month], int(day), t.hour, t.minute, tzinfo=TZ))
            except ValueError:
                pass
        paid = min(candidates, key=lambda x: abs((received-x).total_seconds()))
        if abs((received-paid).total_seconds()) > 86400*2:
            return None
        cents = int(Decimal(amount.replace(',', ''))*100)
        if cents <= 0:
            return None
        return trx, paid.date().isoformat(), 'USD' if currency.strip() in ('$', 'USD') else 'KHR', cents
    except (ValueError, KeyError):
        return None

def database(path):
    db = sqlite3.connect(path)
    db.executescript('''PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS payments(trx TEXT PRIMARY KEY, day TEXT, currency TEXT, cents INTEGER, message_id INTEGER);
    CREATE TABLE IF NOT EXISTS issues(message_id INTEGER PRIMARY KEY, day TEXT, reason TEXT);
    ''')
    return db

def get(db, key, default=None):
    row=db.execute('SELECT value FROM config WHERE key=?',(key,)).fetchone()
    return row[0] if row else default

def put(db,key,value):
    db.execute('INSERT OR REPLACE INTO config VALUES (?,?)',(key,str(value)))

def record(db, payment, message_id):
    old=db.execute('SELECT day,currency,cents FROM payments WHERE trx=?',(payment[0],)).fetchone()
    if old and tuple(old)!=tuple(payment[1:]):
        db.execute('INSERT OR REPLACE INTO issues VALUES (?,?,?)',(message_id,payment[1],'Conflicting transaction ID; original amount retained'))
        return
    db.execute('INSERT OR IGNORE INTO payments VALUES (?,?,?,?,?)',(*payment,message_id))

def report(db, start, end):
    rows=db.execute('SELECT currency,COUNT(*),SUM(cents) FROM payments WHERE day>=? AND day<=? GROUP BY currency',(start,end)).fetchall()
    lines=['📊 ADPrint — PayWay',f'📅 {start}' + (f' → {end}' if end!=start else ''),'សរុបសារទូទាត់ដែល Bot បានកត់ត្រា៖']
    totals={c:(n,v) for c,n,v in rows}
    for c in ('USD','KHR'):
        n,v=totals.get(c,(0,0))
        lines.append(f'{c}: {Decimal(v)/100:,.2f} | {n} ប្រតិបត្តិការ')
    issues=db.execute('SELECT COUNT(*) FROM issues WHERE day>=? AND day<=?',(start,end)).fetchone()[0]
    lines.extend([f'⚠️ សារត្រូវពិនិត្យ៖ {issues}',f'ចាប់ផ្តើមកត់ត្រា៖ {get(db,"started","—")}', 'សរុបនេះតាមសារ Telegram; ផ្ទៀងផ្ទាត់ជាមួយ ABA។'])
    return '\n'.join(lines)

class Bot:
    def __init__(self, token, db, setup_code):
        self.token,self.db,self.setup_code=token,db,setup_code
    def api(self, method, **data):
        request=Request('https://api.telegram.org/bot'+self.token+'/'+method, data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=25) as response:
            body=json.load(response)
        if not body.get('ok'):
            raise RuntimeError('Telegram request failed')
        return body['result']
    def send(self, chat, text):
        self.api('sendMessage',chat_id=chat,text=text)
    def handle(self, update):
        msg=update.get('message') or update.get('edited_message')
        if not msg:
            return
        chat=msg['chat']['id']; sender=msg.get('from',{}); text=msg.get('text','')
        group=get(self.db,'group'); source=get(self.db,'source')
        if text.startswith('/setup ') and not group and not sender.get('is_bot') and msg['chat']['type'] in ('group','supergroup'):
            if not secrets.compare_digest(text.split(' ',1)[1].strip(),self.setup_code):
                return
            member=self.api('getChatMember',chat_id=chat,user_id=sender['id'])
            reply=msg.get('reply_to_message',{})
            author=reply.get('from',{})
            if member['status'] not in ('administrator','creator') or not author.get('is_bot') or not parse_payment(reply.get('text',''),reply.get('date',0)):
                self.send(chat,'Setup: admin must reply to an original PayWay payment text message from the PayWay bot.')
                return
            with self.db:
                put(self.db,'group',chat); put(self.db,'source',author['id'])
                put(self.db,'started',datetime.now(TZ).isoformat(timespec='minutes'))
                put(self.db,'last_report',datetime.now(TZ).date().isoformat())
            self.send(chat,'✅ ភ្ជាប់រួច។ Bot ចាប់ផ្តើមកត់ត្រាសារ PayWay ថ្មី។ /today /yesterday /month /status\nរបាយការណ៍ថ្ងៃមុន ផ្ញើម៉ោង 00:05។ សារចាស់មិនត្រូវបាននាំចូលទេ។')
            return
        if str(chat)!=group:
            return
        if str(sender.get('id'))==source:
            if msg.get('forward_origin'):
                return
            payment=parse_payment(text,msg['date'])
            with self.db:
                if payment:
                    record(self.db,payment,msg['message_id'])
                else:
                    day=datetime.fromtimestamp(msg['date'],TZ).date().isoformat()
                    self.db.execute('INSERT OR REPLACE INTO issues VALUES (?,?,?)',(msg['message_id'],day,'Unrecognized PayWay message; inspect original in Telegram'))
            return
        if sender.get('is_bot') or 'edited_message' in update:
            return
        command=text.split()[0].split('@')[0].lower() if text else ''
        today=datetime.now(TZ).date()
        if command in ('/today','/yesterday','/month'):
            start=end=today
            if command=='/yesterday': start=end=today-timedelta(days=1)
            if command=='/month': start=today.replace(day=1)
            self.send(chat,report(self.db,start.isoformat(),end.isoformat()))
        elif command=='/status':
            self.send(chat,'✅ Bot running\nចាប់ផ្តើម៖ '+get(self.db,'started','—')+'\nរបាយការណ៍ថ្ងៃមុន៖ 00:05 ម៉ោងកម្ពុជា\nមិនរួមបញ្ចូលប្រវត្តិមុនភ្ជាប់ ឬសារដែល Bot មិនបានទទួល។')
    def scheduled(self):
        now=datetime.now(TZ)
        group=get(self.db,'group'); last=get(self.db,'last_report')
        if not group or not last or (now.hour==0 and now.minute<5): return
        day=datetime.fromisoformat(last).date()
        # Catch up full-day reports after restarts; Telegram send can rarely duplicate after a crash.
        if day<now.date():
            self.send(int(group),report(self.db,day.isoformat(),day.isoformat()))
            with self.db: put(self.db,'last_report',(day+timedelta(days=1)).isoformat())

def main():
    token=os.environ['BOT_TOKEN']; setup=os.environ['SETUP_CODE']
    if len(setup)<12: raise SystemExit('SETUP_CODE needs at least 12 characters')
    path=os.environ.get('DB_PATH','data/payway.sqlite3')
    os.makedirs(os.path.dirname(os.path.abspath(path)),exist_ok=True)
    db=database(path); bot=Bot(token,db,setup)
    if bot.api('getWebhookInfo').get('url'):
        raise SystemExit('Existing webhook found. Use a dedicated bot with no webhook.')
    logging.basicConfig(level=logging.INFO)
    for s in (signal.SIGTERM,signal.SIGINT): signal.signal(s,lambda *_:STOP.set())
    logging.info('Worker started; waiting for configured group messages.')
    while not STOP.is_set():
        try:
            updates=bot.api('getUpdates',offset=int(get(db,'offset','0')),timeout=10,allowed_updates=['message','edited_message'])
            for update in updates:
                bot.handle(update)
                with db: put(db,'offset',update['update_id']+1)
            # Report only after draining pending messages, to avoid premature totals.
            if len(updates)<100: bot.scheduled()
        except Exception as exc:
            logging.error('Worker request failed (%s); retrying. No credentials logged.',type(exc).__name__)
            STOP.wait(5)
    db.close()

if __name__=='__main__': main()
