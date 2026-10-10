import unittest
from workflow.sample_coverage import covers_shot

class SampleCoverageTests(unittest.TestCase):
    def test_three_frames(self):
        self.assertTrue(covers_shot([0, 2, 3.96], 4))

    def test_dense_frames(self):
        self.assertTrue(covers_shot([i / 6 for i in range(24)], 4))

    def test_rejects_missing_regions(self):
        for times in ([.3, 2, 4], [0, 1, 4], [0, 2, 3.6]):
            self.assertFalse(covers_shot(times, 4))

    def test_rejects_invalid_samples(self):
        for times in ([0, 2, 2, 4], [0, 4, 2], [-1, 2, 4], [0, 2, 4.1],
                      [0, float('nan'), 4], [0, float('inf'), 4], [False, 2, 4], [0, 4], None):
            self.assertFalse(covers_shot(times, 4))

    def test_rejects_invalid_duration(self):
        for duration in (0, -1, float('nan'), float('inf'), True):
            self.assertFalse(covers_shot([0, 1, 2], duration))
