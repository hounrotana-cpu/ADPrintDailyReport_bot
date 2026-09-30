import unittest
from datetime import datetime
from main import parse_payment, database, record, report, TZ

class Tests(unittest.TestCase):
    def msg(self,amount='$55.00',trx='123',date='Sep 30, 11:33 AM'):
        return f'{amount} paid by Test Customer (*029) on {date} via ABA KHQR (Bank) at ADPrint. Trx. ID: {trx}, APV: 311393.'
    def test_parse_and_deduplicate(self):
        stamp=datetime(2026,9,30,12,tzinfo=TZ).timestamp()
        p=parse_payment(self.msg(),stamp)
        self.assertEqual(p,('123','2026-09-30','USD',5500))
        db=database(':memory:')
        record(db,p,1);record(db,p,2)
        record(db,('124','2026-09-30','KHR',100000),3)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM payments').fetchone()[0],2)
        record(db,('123','2026-09-30','USD',6000),1)
        self.assertEqual(db.execute('SELECT cents FROM payments WHERE trx="123"').fetchone()[0],5500)
        self.assertIn('USD: 55.00',report(db,'2026-09-30','2026-09-30'))
        self.assertEqual(db.execute('SELECT COUNT(*) FROM issues').fetchone()[0],1)
    def test_year_rollover_and_bad_formats(self):
        stamp=datetime(2027,1,1,0,1,tzinfo=TZ).timestamp()
        p=parse_payment(self.msg(date='Dec 31, 11:59 PM'),stamp)
        self.assertEqual(p[1],'2026-12-31')
        self.assertIsNone(parse_payment(self.msg(amount='$1,2.00',date='Dec 31, 11:59 PM'),stamp))
        self.assertIsNone(parse_payment('Refund $55',stamp))
    def test_decimal(self):
        stamp=datetime(2026,9,30,12,tzinfo=TZ).timestamp()
        self.assertEqual(parse_payment(self.msg(amount='$0.29'),stamp)[3],29)

if __name__=='__main__':unittest.main()
