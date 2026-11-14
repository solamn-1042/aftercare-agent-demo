import json
import unittest
from service.ticket_engine import TicketEngine
HIT={"source":"after_sales.md","chunk_id":"after_sales.md#chunk_002","text":"演示政策","score":.9,"method":"chroma"}

def tool(name,args,id='t1'):
    return {"role":"assistant","tool_calls":[{"id":id,"type":"function","function":{"name":name,"arguments":json.dumps(args)}}]}

class TicketTests(unittest.TestCase):
    def engine(self,messages=None):
        iterator=iter(messages or [])
        return TicketEngine(None,None,api_key='',vector_fn=lambda *a,**k:[dict(HIT)],completion_fn=lambda _:next(iterator))

    def test_owned_order(self):
        self.assertEqual(self.engine().execute('get_order','{"order_id":"A001"}')['status'],'已发货')

    def test_cross_customer_denied(self):
        self.assertEqual(self.engine().execute('get_order','{"order_id":"B001"}'),{'error':'ORDER_NOT_FOUND_OR_FORBIDDEN'})

    def test_missing_indistinguishable_from_forbidden(self):
        e=self.engine()
        self.assertEqual(e.execute('get_order','{"order_id":"A999"}'),e.execute('get_order','{"order_id":"B001"}'))

    def test_customer_cannot_be_overridden(self):
        self.assertEqual(self.engine().execute('get_order','{"order_id":"B001","customer":"demo-bob"}')['error'],'INVALID_ARGUMENTS')

    def test_json_and_types_validated(self):
        for raw in ['not json','[]','null','{"order_id":1}','{"order_id":"../secret"}']:
            self.assertIn('error',self.engine().execute('get_order',raw))

    def test_unknown_tool(self):
        self.assertEqual(self.engine().execute('shell','{}')['error'],'UNKNOWN_TOOL')

    def test_order_graph(self):
        e=self.engine([tool('get_order',{'order_id':'A001'}),{'role':'assistant','content':'已发货[订单: A001]'}])
        r=e.ask('A001')
        self.assertEqual(r['status'],'answered')
        self.assertEqual(r['tool_count'],1)

    def test_fabricated_order_citation_blocked(self):
        e=self.engine([{'role':'assistant','content':'待发货[订单: B001]'}])
        self.assertEqual(e.ask('B001')['status'],'refused')

    def test_placeholder_order_citation_blocked_even_with_valid_policy(self):
        e=self.engine([tool('search_policy',{'question':'退货'}),{'role':'assistant','content':'政策[来源: after_sales.md#chunk_002] [订单: 待提供订单号]'}])
        self.assertEqual(e.ask('退货')['status'],'refused')

    def test_policy_tool_and_citation(self):
        e=self.engine([tool('search_policy',{'question':'退货'}),{'role':'assistant','content':'政策[来源: after_sales.md#chunk_002]'}])
        self.assertEqual(e.ask('退货')['status'],'answered')

    def test_model_call_budget(self):
        e=self.engine([tool('get_order',{'order_id':'A001'},str(i)) for i in range(4)])
        r=e.ask('无限循环')
        self.assertEqual(r['status'],'budget_exhausted')
        self.assertEqual(r['tool_count'],3)

    def test_multiple_tools(self):
        msg=tool('get_order',{'order_id':'A002'})
        msg['tool_calls']+=tool('search_policy',{'question':'退款'},'t2')['tool_calls']
        e=self.engine([msg,{'role':'assistant','content':'退款中[订单: A002]；政策[来源: after_sales.md#chunk_002]'}])
        self.assertEqual(e.ask('退款')['tool_count'],2)

    def test_no_model_claim_without_evidence(self):
        self.assertEqual(self.engine([{'role':'assistant','content':'明天一定到账'}]).ask('退款')['status'],'refused')
