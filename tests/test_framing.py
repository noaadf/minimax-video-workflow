import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageDraw
from workflow import media
from workflow.framing import POLICY, filter_for
from workflow.narrative_gate import AUTHORED_FIELDS


class FramingTests(unittest.TestCase):
    def test_authored_policy_and_invalid_rejected(self):
        self.assertIn('framing_policy', AUTHORED_FIELDS)
        self.assertIn('crop=', filter_for(540, 960))
        with self.assertRaises(ValueError):
            filter_for(540, 960, 'random')

    def test_real_landscape_edges_preserved_in_preview_and_production(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            im = Image.new('RGB', (640, 360), 'green')
            draw = ImageDraw.Draw(im)
            draw.rectangle((0, 0, 99, 359), fill='red')
            draw.rectangle((540, 0, 639, 359), fill='blue')
            im.save(p/'source.png')
            media.ff('-loop', '1', '-i', p/'source.png', '-t', .5, '-r', 24,
                     '-c:v', 'libx264', '-pix_fmt', 'yuv420p', p/'source.mp4')
            media.ff('-i', p/'source.mp4', '-vf', filter_for(540, 960, POLICY),
                     '-frames:v', 1, p/'preview.png')
            media.normalize_shot(p/'source.mp4', p/'normalized.mp4', .5, framing_policy=POLICY)
            media.ff('-i', p/'normalized.mp4', '-frames:v', 1, p/'production.png')
            for name, width, height in [('preview.png',540,960),('production.png',1080,1920)]:
                frame = Image.open(p/name).convert('RGB')
                self.assertEqual(frame.size, (width,height))
                red=frame.getpixel((int(width*.05),height//2))
                blue=frame.getpixel((int(width*.95),height//2))
                self.assertGreater(red[0],200);self.assertLess(red[2],30)
                self.assertGreater(blue[2],200);self.assertLess(blue[0],30)
                self.assertLess(max(frame.getpixel((width//2,10))),10)
