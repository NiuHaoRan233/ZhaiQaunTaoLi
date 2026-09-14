import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_gold_intraday_value import validate_config,EXPECTED_CONFIG,CONFIG,read


class DeliveryTests(unittest.TestCase):
    def test_shipped_config_matches_frozen_model(self):
        validate_config(read(CONFIG))

    def test_changed_capital_or_fee_cannot_silently_use_old_model(self):
        for key,value in [('capital_per_code_cny',150000),('fee_per_side_cny',0),('fast_iv_seconds',5),('date','20260914')]:
            with self.assertRaises(ValueError):validate_config(dict(EXPECTED_CONFIG,**{key:value}))


if __name__=='__main__':unittest.main()
