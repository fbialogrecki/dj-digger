from scripts.benchmark_analysis import metrics, summarize


def test_benchmark_separates_octave_errors_abstention_and_unverified_references():
    assert metrics({'bpm': 60, 'key': 'Dbm'}, {'bpm': 120, 'key': 'C#m'}) == {
        'bpm_error_percent': 50.0, 'bpm_within_2_percent': False,
        'tempo_half': True, 'tempo_double': False, 'key_exact': True,
    }
    rows = [
        {'reference': {'bpm': 120, 'verified': True}, 'result': {'bpm': 120},
         'comparison': {'bpm_within_2_percent': True}},
        {'reference': {'bpm': 120, 'verified': True}, 'result': {'bpm': None}},
        {'reference': {'bpm': 120, 'verified': False}, 'result': {'bpm': 120},
         'comparison': {'bpm_within_2_percent': True}},
    ]
    assert summarize(rows)['bpm'] == {'verified_references': 2, 'answered': 1, 'correct': 1,
                                   'missing_or_failed': 1, 'accuracy_all_references': .5,
                                   'accuracy_answered': 1.0}
