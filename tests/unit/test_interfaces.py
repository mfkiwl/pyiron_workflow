from pathlib import Path
import sys
from unittest import TestCase, skipUnless


from pyiron_workflow.interfaces import Creator


@skipUnless(
    sys.version_info[0] == 3 and sys.version_info[1] >= 10, "Only supported for 3.10+"
)
class TestCreator(TestCase):
    def test_registration(self):
        creator = Creator()

        with self.assertRaises(
            AttributeError,
            msg="Sanity check that the package isn't there yet and the test setup is "
                "what we want"
        ):
            creator.demo_nodes

        path_to_tests = Path(__file__).parent.parent
        sys.path.append(str(path_to_tests.resolve()))
        creator.register("demo", "static.demo_nodes")

        node = creator.demo.Add(1, 2)
        self.assertEqual(
            3,
            node(),
            msg="Node should get instantiated from creator and be operable"
        )
