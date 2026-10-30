import unittest
from types import SimpleNamespace as Obj
from unittest.mock import Mock
from FunPayAPI.common.enums import MessageTypes as MT
from Utils.confirmation_review import assess


def msg(id,author,text='',type=MT.NON_SYSTEM,staff=False):
    return Obj(id=id,author_id=author,text=text,type=type,is_employee=staff)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.order=Obj(id='ORDER123',chat_id=55,buyer_id=9,buyer_username='buyer',review=None)
        self.account=Obj(id=7,get_chat_history=Mock())
        self.start=msg(1,0,'Paid #ORDER123',MT.ORDER_PURCHASED)
    def check(self,*messages):
        self.account.get_chat_history.return_value=[self.start,*messages]
        return assess(self.account,self.order)
    def test_silent_and_thanks_eligible(self):
        self.assertEqual(self.check(msg(2,7,'Credentials delivered')),'')
        self.assertEqual(self.check(msg(2,9,'Thanks!')),'')
    def test_problem_and_ambiguous_buyer_replies_held(self):
        self.assertEqual(self.check(msg(2,9,'Password is invalid')),'buyer_problem')
        self.assertEqual(self.check(msg(2,9,'Не работает вход')),'buyer_problem')
        self.assertEqual(self.check(msg(2,9,'Can you explain this?')),'buyer_review')
        self.assertEqual(self.check(msg(2,9,'')),'buyer_review')
    def test_support_and_reopened_held(self):
        self.assertEqual(self.check(msg(2,500,'Checking',staff=True)),'support')
        self.assertEqual(self.check(msg(2,0,'Reopened',MT.ORDER_REOPENED)),'reopened')
    def test_later_order_does_not_contaminate_current_order(self):
        other=msg(3,0,'Paid #OTHER123',MT.ORDER_PURCHASED)
        self.assertEqual(self.check(msg(2,9,'Thanks'),other,msg(4,500,'Checking',staff=True)),'')
        self.assertEqual(self.check(other,msg(4,500,'Issue with #ORDER123',staff=True)),'support')
    def test_missing_history_fails_closed_and_pagination_works(self):
        self.account.get_chat_history.return_value=[]
        self.assertEqual(assess(self.account,self.order),'history_unavailable')
        self.account.get_chat_history.side_effect=[[msg(5,9,'Thanks')],[self.start,msg(2,7,'Delivered')]]
        self.assertEqual(assess(self.account,self.order),'')
    def test_poor_review_held_even_without_history(self):
        self.order.review=Obj(stars=2)
        self.assertEqual(assess(self.account,self.order),'review_rating')
        self.account.get_chat_history.assert_not_called()
