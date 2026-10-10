import unittest
from unittest.mock import patch
from workflow.media import review_contact

class ContactDurationTests(unittest.TestCase):
    def test_long_film_reaches_final_second(self):
        with patch('workflow.media.probe', return_value={'format': {'duration': '50.5'}}), patch('workflow.media.ff') as ff:
            review_contact('video.mp4', 'sheet.jpg')
        vf=ff.call_args.args[3]
        interval=float(vf.split('selected_n*')[1].split(')')[0])
        self.assertGreater(interval*14, 50)
        self.assertLess(interval*14, 50.5)
        self.assertIn('tile=5x3',vf)

    def test_invalid_duration_rejected(self):
        for duration in ['0','nan','-1']:
            with self.subTest(duration=duration), patch('workflow.media.probe', return_value={'format': {'duration':duration}}), patch('workflow.media.ff') as ff:
                with self.assertRaises(ValueError): review_contact('video.mp4','sheet.jpg')
                ff.assert_not_called()

if __name__=='__main__':unittest.main()
