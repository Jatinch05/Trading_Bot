import pandas as pd
from services.validation.validate import normalize_and_validate

def test_validation_enforces_nrml_and_link_tag():
    df = pd.DataFrame([{
        "symbol":"NIFTY25JANFUT","exchange":"NFO","txn_type":"BUY","qty":1,
        "order_type":"MARKET","price":None,"trigger_price":None,
        "product":"MIS","validity":"DAY","variety":"regular","disclosed_qty":0,
        "tag":"link:g1","gtt":"NO","gtt_type":None,
        "gtt_trigger":None,"gtt_limit":None,
        "gtt_trigger_1":None,"gtt_limit_1":None,"gtt_trigger_2":None,"gtt_limit_2":None,
    }])
    intents, vdf, errs = normalize_and_validate(df, instruments=None)
    assert not errs
    assert intents[0].product == "NRML"
    assert intents[0].tag == "link:g1"
