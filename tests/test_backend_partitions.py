import copy
import unittest

from workflow.backends import members


class PartitionTests(unittest.TestCase):
    def config(self):
        base = {'type': 'ascend_ssh', 'host': 'worker', 'port': 22, 'resource_id': 'one',
                'ready': True, 'ready_for_dispatch': True, 'expires_at': '2099-01-01T00:00:00+00:00',
                'logical_devices': 8, 'physical_cards': 4, 'resource_logical_devices': 16,
                'resource_physical_cards': 8}
        return {'type': 'pool', 'groups': [
            {**base, 'capability': 'slot0', 'api_port': 30088, 'remote_root': '/work/slot0', 'logical_device_ids': list(range(8))},
            {**base, 'capability': 'slot1', 'api_port': 30089, 'remote_root': '/work/slot1', 'logical_device_ids': list(range(8, 16))}]}

    def test_disjoint_full_card_partitions_share_notebook(self):
        pool = self.config(); self.assertEqual(len(members(pool)), 2)
        pool['groups'][1]['ready'] = False
        self.assertEqual(len(members(pool)), 1)

    def test_missing_or_overlapping_partition_rejected(self):
        for key, value in [('logical_device_ids', list(range(8))), ('api_port', 30088),
                           ('remote_root', '/work/slot0/'), ('host', 'other'),
                           ('physical_cards', 8), ('logical_device_ids', [True] + list(range(9, 16))),
                           ('resource_logical_devices', 32), ('resource_id', 'another')]:
            pool = self.config(); pool['groups'][1][key] = value
            with self.assertRaises(ValueError): members(pool)
        pool = self.config(); del pool['groups'][1]['logical_device_ids']
        with self.assertRaises(ValueError): members(pool)

    def test_no_two_services_share_chips_of_one_physical_card(self):
        pool = self.config()
        pool['groups'][0].update(logical_device_ids=list(range(0, 16, 2)), physical_cards=8)
        pool['groups'][1].update(logical_device_ids=list(range(1, 16, 2)), physical_cards=8)
        with self.assertRaises(ValueError): members(pool)

    def test_legacy_aliases_still_rejected(self):
        pool = self.config()
        for g in pool['groups']:
            for k in ['logical_device_ids', 'api_port', 'resource_logical_devices', 'resource_physical_cards']: g.pop(k)
        with self.assertRaises(ValueError): members(pool)
