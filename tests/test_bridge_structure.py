import copy
import unittest
from scripts.render_bridge_structure import geometry, check


class BridgeStructureTests(unittest.TestCase):
    def test_valid_scene_has_connected_vertical_hangers(self):
        self.assertTrue(all(check(geometry()).values()))

    def test_wrong_hanger_direction_is_rejected(self):
        model = geometry()
        model['nodes']['near_c1'][0] += .1
        with self.assertRaises(ValueError):
            check(model)

    def test_collapsed_cable_planes_are_rejected(self):
        model = geometry()
        for key in model['main_cables'][1]:
            model['nodes'][key][1] = -.75
        with self.assertRaises(ValueError):
            check(model)

    def test_missing_hanger_to_truss_connection_is_rejected(self):
        model = geometry()
        model['members'] = [e for e in model['members'] if not (
            e['kind'] == 'truss' and 'near_d1' in (e['a'], e['b']))]
        with self.assertRaises(ValueError):
            check(model)
