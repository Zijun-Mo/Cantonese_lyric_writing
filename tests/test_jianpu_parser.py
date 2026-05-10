import unittest

from src.preprocess.jianpu_parser import parse_jianpu


class JianpuParserTest(unittest.TestCase):
    def test_slur_group_counts_as_one_slot(self):
        score = parse_jianpu("(1 2 3) 4")

        self.assertEqual(score.bars[0].slot_count, 2)
        notes = score.bars[0].singable_notes
        self.assertEqual([n.pitch for n in notes], [1, 4])
        self.assertEqual(notes[0].duration, 3.0)

    def test_slur_group_can_cross_bar(self):
        score = parse_jianpu("(1 2 | 3 4) 5")

        self.assertEqual(score.bars[0].slot_count, 1)
        self.assertEqual(score.bars[1].slot_count, 1)
        self.assertEqual(score.total_slots, 2)
        self.assertEqual(score.bars[0].singable_notes[0].pitch, 1)
        self.assertEqual(score.bars[0].singable_notes[0].duration, 4.0)
        self.assertEqual(score.bars[1].singable_notes[0].pitch, 5)


if __name__ == "__main__":
    unittest.main()
