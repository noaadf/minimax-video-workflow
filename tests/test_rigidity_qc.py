import unittest
try:
    import numpy as np
    import cv2
    from workflow.rigidity_qc import residuals,scan
    AVAILABLE=True
except ImportError:
    AVAILABLE=False

@unittest.skipUnless(AVAILABLE,'Optional motion runtime: run with .venv-research/bin/python')
class RigidityTests(unittest.TestCase):
    def setUp(self):
        self.points=np.array([[x,y] for y in range(30,450,30) for x in range(20,250,20)],dtype=np.float32)
    def test_uniform_camera_push_is_not_local_shape_change(self):
        current=self.points*1.025+np.array([2,-3],dtype=np.float32)
        self.assertLess(residuals(self.points,current)['p90_pixels'],.01)
    def test_twisted_half_has_local_residual(self):
        current=self.points.copy();current[current[:,0]>140,0]+=10
        result=residuals(self.points,current)
        self.assertGreater(result['p90_pixels'],2)
    def test_noisy_compression_small_jitter_not_motion_alarm(self):
        noise=np.random.default_rng(1).normal(0,.15,self.points.shape).astype(np.float32)
        self.assertLess(residuals(self.points,self.points+noise)['p90_pixels'],1)

    def test_encoded_video_controls_static_and_local_warp(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            rng=np.random.default_rng(42)
            frame=np.zeros((480,270,3),dtype=np.uint8)
            for _ in range(350):
                x,y=rng.integers([15,25],[255,455]);color=tuple(int(v) for v in rng.integers(50,255,3))
                cv2.rectangle(frame,(int(x),int(y)),(int(x+5),int(y+5)),color,-1)
            for kind in ['static','warp']:
                writer=cv2.VideoWriter(str(root/f'{kind}.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),24,(270,480))
                self.assertTrue(writer.isOpened())
                for n in range(24):
                    current=frame.copy()
                    if kind=='warp' and n>=8:
                        current[:,140:]=cv2.warpAffine(frame,np.float32([[1,0,8],[0,1,0]]),(270,480))[:,140:]
                    writer.write(current)
                writer.release()
            baseline=scan(root/'static.mp4',duration=1)
            warped=scan(root/'warp.mp4',duration=1)
            self.assertTrue(baseline['decode_complete']);self.assertEqual(baseline['exception_intervals'],[])
            self.assertTrue(warped['exception_intervals'])
            self.assertFalse(warped['topology_certified'])
