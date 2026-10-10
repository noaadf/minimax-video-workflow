import unittest
from scripts.render_bridge_traction import state, checks, TRACTION_FRAMES


class TractionTests(unittest.TestCase):
    def states(self):
        return [state(i) for i in range(TRACTION_FRAMES)]

    def test_loop_motion_and_tow_coupling(self):
        self.assertTrue(all(checks(self.states()).values()))

    def test_detached_strand_fails(self):
        states = self.states(); states[20]['strand_end'][0] += 10
        with self.assertRaises(ValueError): checks(states)

    def test_rogue_puller_fails(self):
        states = self.states(); states[20]['puller'][1] += 10
        with self.assertRaises(ValueError): checks(states)

    def test_wrong_wheel_rotation_fails(self):
        states = self.states(); states[20]['wheel_angle'] *= -1
        with self.assertRaises(ValueError): checks(states)
