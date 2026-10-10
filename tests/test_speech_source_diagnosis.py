import unittest

try:
    import numpy as np
    from scripts.diagnose_speech_sources import waveform_match
except ImportError:
    np = None


@unittest.skipIf(np is None, 'optional scientific runtime')
class SourceWaveformTests(unittest.TestCase):
    def test_scaled_delayed_source_matches(self):
        x = np.random.default_rng(14).normal(size=16000)
        y = np.r_[np.zeros(80), x[:-80] * .4]
        result = waveform_match(x, y)
        self.assertTrue(result['waveform_correspondence'])
        self.assertEqual(result['lag_samples'], 80)

    def test_different_source_rejected(self):
        rng = np.random.default_rng(12)
        self.assertFalse(waveform_match(rng.normal(size=16000), rng.normal(size=16000))['waveform_correspondence'])

    def test_changing_loudness_does_not_mean_different_words(self):
        x = np.random.default_rng(15).normal(size=16000)
        y = x*np.linspace(.1, 2, len(x))
        result = waveform_match(x, y)
        self.assertLess(result['correlation'], .98)
        self.assertTrue(result['waveform_correspondence'])

    def test_silence_is_unknown(self):
        result = waveform_match(np.zeros(16000), np.zeros(16000))
        self.assertFalse(result['waveform_correspondence'])
        self.assertIsNone(result['correlation'])
