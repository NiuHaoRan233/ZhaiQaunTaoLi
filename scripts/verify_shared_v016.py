"""Verify bounded default-preserving edits and reproduce immutable comparisons."""
import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from zhaiquant import maker_paper
from zhaiquant.config import load_config
from zhaiquant.one_hand_maker_research import replay_one_hand_day
from zhaiquant.shared_thousand_maker_v013_research import SharedThousandV013Allocator
from zhaiquant.shared_thousand_maker_v014_research import economic_fills
from zhaiquant.shared_thousand_maker_v015_research import AllocationParametersV015
from zhaiquant.shared_thousand_maker_v015r2_research import SharedThousandV015R2Allocator


def verify_sources():
    frozen = json.loads(Path('output/research/shared_v016_parent_freeze_20260907.json').read_text(encoding='utf-8'))
    for name, fields in frozen['profiles'].items():
        assert json.loads(json.dumps(asdict(getattr(maker_paper, name)))) == fields, name
    for p, digest in frozen['source_hashes'].items():
        if p not in frozen['modified_source_text']:
            assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest, p
    core = Path('src/zhaiquant/maker_paper.py').read_text(encoding='utf-8')
    start = core.index('SHARED_THOUSAND_POLICY_V016_CANDIDATE = replace(')
    end = core.index('QUEUE_POLICY_V10 = MakerPolicyProfile(', start)
    core = core[:start]+core[end:]
    guard = '''        if (
            self.priority_policy.model_id == "maker_shared_1000_v0_16_candidate"
            and not getattr(self, "causal_shared_execution", False)
        ):
            raise ValueError("v0.16 requires CausalSharedMakerEngine")
'''
    assert core.count(guard) == 1
    core = core.replace(guard, '')
    assert core == frozen['modified_source_text']['src/zhaiquant/maker_paper.py']
    replay = Path('src/zhaiquant/one_hand_maker_research.py').read_text(encoding='utf-8')
    replay = replay.replace('    engine_class: type[MakerPaperEngine] = MakerPaperEngine,\n', '')
    replay = replay.replace('engines[code] = engine_class(', 'engines[code] = MakerPaperEngine(')
    assert replay == frozen['modified_source_text']['src/zhaiquant/one_hand_maker_research.py']
    return dict(old_profiles=len(frozen['profiles']), old_sources_exact=True, default_edits_exact=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    path = Path(args.output)
    if path.exists():
        raise FileExistsError(path)
    result = dict(source_checks=verify_sources(), baseline_checks=[], candidate_checks=[])
    config = load_config('config.toml')
    old = json.loads(Path('output/research/shared_v015r2_matrix_20260804_20260904.json').read_text(encoding='utf-8'))
    for pair in old['cells']:
        day = pair['market_date']
        if day not in ('2026-08-07','2026-08-17','2026-08-20'):
            continue
        for label, policy, allocator, parameters in (
            ('reference', maker_paper.SHARED_THOUSAND_POLICY_V013_CANDIDATE, SharedThousandV013Allocator, None),
            ('parent', maker_paper.SHARED_THOUSAND_POLICY_V014_CANDIDATE, SharedThousandV013Allocator, None),
            ('candidate', maker_paper.SHARED_THOUSAND_POLICY_V015_R2_CANDIDATE, SharedThousandV015R2Allocator, AllocationParametersV015()),
        ):
            cell = replay_one_hand_day(config, market_date=day, priority_policy=policy,
                allocator_class=allocator, parameters=parameters, shared_capacity_bonds=1000)
            assert economic_fills(cell) == economic_fills(pair[label]), (day, label, 'fills')
            for key in ('trading_pnl', 'order_counts', 'terminal_inventory_bonds', 'equivalent_full_slot_exposure_seconds'):
                assert cell[key] == pair[label][key], (day, label, key)
            result['baseline_checks'].append(dict(day=day, model_id=policy.model_id, exact=True))
            print(day, policy.model_id, 'exact', flush=True)
    for name in ('shared_v016_matrix_20260804_20260904.json','shared_v016_target_20260907_144500.json'):
        report = json.loads((Path('output/research')/name).read_text(encoding='utf-8'))
        for cell in report['cells']:
            candidate = cell['candidate']
            cash = candidate['initial_cash_cny']
            inventory = {}
            for fill in candidate['fills']:
                sign = 1 if fill['side'] == 'buy' else -1
                cash -= sign*fill['quantity']*fill['price']
                code = fill['bond_code']
                inventory[code] = inventory.get(code, 0)+sign*fill['quantity']
                assert cash >= -1e-6 and 0 <= sum(inventory.values()) <= 1000+1e-9
                assert sum(q > 1e-9 for q in inventory.values()) <= 1
            reconstructed = cash+candidate['terminal_mark_value_cny']-candidate['initial_cash_cny']
            assert abs(reconstructed-candidate['trading_pnl']) < 1e-6
            assert all(a['parent_model_id'] == maker_paper.SHARED_THOUSAND_POLICY_V014_CANDIDATE.model_id
                       and a['model_id'] == 'maker_shared_1000_v0_16_candidate' for a in candidate['model_assignments'])
            for engine in candidate['causal_execution_metrics']['engines'].values():
                for audit in engine['active_execution_audit']:
                    assert audit['filled'] <= audit['executable']+1e-9 <= audit['requested']+2e-9
            result['candidate_checks'].append(dict(day=cell['market_date'], pnl=candidate['trading_pnl'], ledger_exact=True))
    with path.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(output=str(path), checks=len(result['candidate_checks']))))


if __name__ == '__main__':
    main()
