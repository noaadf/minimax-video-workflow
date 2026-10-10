import unittest
from workflow.prompt_grammar import lint


class PromptGrammarTest(unittest.TestCase):
    def test_good_keyframe_passes(self):
        p = ('化验台样品近景情景演绎：玻璃器皿与两份白色碳酸盐粉末样品，顶部漫射光，冷暖对比，'
             '纪录片实拍质感；日期、产能与纯度数字由口播与字幕承载。')
        self.assertEqual(lint(p), [])

    def test_missing_framing_fails(self):
        p = '一个实验室里面有很多器皿和样品，光线柔和，质感真实，不出现文字。' + '细节丰富。' * 8
        self.assertTrue(any('framing' in v for v in lint(p)))

    def test_schematic_wording_fails(self):
        p = ('萃取流程示意图全景：从原料到产品的全部装置排布，顶光照明，工业质感；'
             '不出现文字与图表。' + '装置细节具体。' * 6)
        self.assertTrue(any('schematic' in v for v in lint(p)))

    def test_real_production_prompt_passes(self):
        # author_2340 shipped prompt verbatim: ban clause names 流程图 but is not schematic itself.
        p = ('传统槽式萃取厂房全景情景演绎：成排大型混合澄清槽体与密集管道，灰暗工业照明；'
             '不出现流程图与文字。')
        self.assertEqual(lint(p), [])

    def test_temporal_wording_fails(self):
        p = ('中景情景演绎：操作员演示萃取全过程，侧逆光，纪录片质感；不出现文字数字。' + '动作细节。' * 6)
        self.assertTrue(any('temporal' in v for v in lint(p)))

    def test_text_risk_requires_ban(self):
        p = ('近景：化验台上摆着贴有标签的样品瓶与报表数字，暖钨丝灯，实拍质感，' + '细节更多。' * 6)
        self.assertTrue(any('text risk' in v for v in lint(p)))
        p_ok = p + '文字与数字由口播与字幕承载。'
        self.assertFalse(any('text risk' in v for v in lint(p_ok)))

    def test_video_prompt_contract(self):
        p = ('A slow push-in over the stationary skull model on the workbench, scan light gliding. '
             'Begin exactly at the first reference frame and end exactly at the last reference frame. No cuts.')
        self.assertEqual(lint(p, kind='video'), [])
        short = 'Camera moves. Reference frame binding. No cuts.'
        self.assertTrue(lint(short, kind='video'))


if __name__ == '__main__':
    unittest.main()
