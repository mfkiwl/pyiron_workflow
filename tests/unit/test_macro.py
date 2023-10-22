from functools import partialmethod
import unittest
from sys import version_info

from pyiron_workflow.channels import NotData
from pyiron_workflow.function import SingleValue
from pyiron_workflow.macro import Macro


def add_one(x):
    result = x + 1
    return result


def add_three_macro(macro):
    macro.one = SingleValue(add_one)
    SingleValue(add_one, macro.one, label="two", parent=macro)
    macro.add(SingleValue(add_one, macro.two, label="three"))
    # Cover a handful of addition methods,
    # although these are more thoroughly tested in Workflow tests


@unittest.skipUnless(version_info[0] == 3 and version_info[1] >= 10, "Only supported for 3.10+")
class TestMacro(unittest.TestCase):

    def test_labels(self):
        m = Macro(add_three_macro)
        self.assertEqual(
            m.label,
            add_three_macro.__name__,
            msg="Label should be automatically generated"
        )
        label = "custom_name"
        m2 = Macro(add_three_macro, label=label)
        self.assertEqual(m2.label, label, msg="Should be able to specify a label")

    def test_wrapper_function(self):
        m = Macro(add_three_macro)

        self.assertIs(
            m.outputs.three__result.value,
            NotData,
            msg="Output should be accessible with the usual naming convention, but we "
                "have not run yet so there shouldn't be any data"
        )

        input_x = 1
        expected_value = add_one(add_one(add_one(input_x)))
        out = m(one__x=input_x)  # Take kwargs to set input at runtime

        self.assertEqual(
            out.three__result,
            expected_value,
            msg="Macros should return the output, just like other nodes"
        )
        self.assertEqual(
            m.outputs.three__result.value,
            expected_value,
            msg="Macros should get output updated, just like other nodes"
        )

    def test_subclass(self):
        class MyMacro(Macro):
            def build_graph(self):
                add_three_macro(self)

            __init__ = partialmethod(
                Macro.__init__,
                build_graph,
            )

        x = 0
        m = MyMacro(one__x=x)
        m.run()
        self.assertEqual(
            m.outputs.three__result.value,
            add_one(add_one(add_one(x))),
            msg="Subclasses should be able to simply override the graph_creator arg"
        )

    def test_key_map(self):
        m = Macro(
            add_three_macro,
            inputs_map={"one__x": "my_input"},
            outputs_map={
                "three__result": "my_output",
                "two__result": "intermediate"
            },
        )
        self.assertSetEqual(
            set(m.inputs.labels),
            set(("my_input",)),
            msg="Input should be relabelled, but not added to or taken away from"
        )
        self.assertSetEqual(
            set(m.outputs.labels),
            set(("my_output", "intermediate")),
            msg="Output should be relabelled and expanded"
        )

        with self.subTest("Make new names can be used as usual"):
            x = 0
            out = m(my_input=x)
            self.assertEqual(
                out.my_output,
                add_one(add_one(add_one(x))),
                msg="Expected output but relabeled should be accessible"
            )
            self.assertEqual(
                out.intermediate,
                add_one(add_one(x)),
                msg="New, internally connected output that was specifically requested "
                    "should be accessible"
            )

        with self.subTest("IO can be disabled"):
            m = Macro(
                add_three_macro,
                inputs_map={"one__x": None},
                outputs_map={"three__result": None},
            )
            self.assertEqual(
                len(m.inputs.labels),
                0,
                msg="Only inputs should have been disabled"
            )
            self.assertEqual(
                len(m.outputs.labels),
                0,
                msg="Only outputs should have been disabled"
            )

    def test_nesting(self):
        def nested_macro(macro):
            macro.a = SingleValue(add_one)
            macro.b = Macro(
                add_three_macro,
                one__x=macro.a,
                outputs_map={"two__result": "intermediate_result"}
            )
            macro.c = Macro(
                add_three_macro,
                one__x=macro.b.outputs.three__result,
                outputs_map={"two__result": "intermediate_result"}
            )
            macro.d = SingleValue(
                add_one,
                x=macro.c.outputs.three__result,
            )
            macro.a > macro.b > macro.c > macro.d
            macro.starting_nodes = [macro.a]
            # This definition of the execution graph is not strictly necessary in this
            # simple DAG case; we just do it to make sure nesting definied/automatic
            # macros works ok
            macro.outputs_map = {"b__intermediate_result": "deep_output"}

        m = Macro(nested_macro)
        self.assertEqual(m(a__x=0).d__result, 8)

        m2 = Macro(nested_macro)

        with self.subTest("Test Node.get_parent_proximate_to"):
            self.assertIs(
                m.b,
                m.b.two.get_parent_proximate_to(m),
                msg="Should return parent closest to the passed composite"
            )

            self.assertIsNone(
                m.b.two.get_parent_proximate_to(m2),
                msg="Should return None when composite is not in parentage"
            )

        with self.subTest("Test Node.get_first_shared_parent"):
            self.assertIs(
                m.b,
                m.b.two.get_first_shared_parent(m.b.three),
                msg="Should get the parent when parents are the same"
            )
            self.assertIs(
                m,
                m.b.two.get_first_shared_parent(m.c.two),
                msg="Should find first matching object in parentage"
            )
            self.assertIs(
                m,
                m.b.two.get_first_shared_parent(m.d),
                msg="Should work when depth is not equal"
            )
            self.assertIsNone(
                m.b.two.get_first_shared_parent(m2.b.two),
                msg="Should return None when no shared parent exists"
            )
            self.assertIsNone(
                m.get_first_shared_parent(m.b),
                msg="Should return None when parent is None"
            )

    def test_execution_automation(self):
        fully_automatic = add_three_macro

        def fully_defined(macro):
            add_three_macro(macro)
            macro.one > macro.two > macro.three
            macro.starting_nodes = [macro.one]

        def only_order(macro):
            add_three_macro(macro)
            macro.two > macro.three

        def only_starting(macro):
            add_three_macro(macro)
            macro.starting_nodes = [macro.one]

        m_auto = Macro(fully_automatic)
        m_user = Macro(fully_defined)

        x = 0
        expected = add_one(add_one(add_one(x)))
        self.assertEqual(
            m_auto(one__x=x).three__result,
            expected,
            "DAG macros should run fine without user specification of execution."
        )
        self.assertEqual(
            m_user(one__x=x).three__result,
            expected,
            "Macros should run fine if the user nicely specifies the exeuction graph."
        )

        with self.subTest("Partially specified execution should fail"):
            # We don't yet check for _crappy_ user-defined execution,
            # But we should make sure it's at least valid in principle
            with self.assertRaises(ValueError):
                Macro(only_order)

            with self.assertRaises(ValueError):
                Macro(only_starting)

    def test_replace_node(self):
        macro = Macro(add_three_macro)

        adds_three_node = Macro(
            add_three_macro,
            inputs_map={"one__x": "x"},
            outputs_map={"three__result": "result"}
        )
        adds_one_node = macro.two

        self.assertEqual(
            macro(one__x=0).three__result,
            3,
            msg="Sanity check"
        )

        with self.subTest("Verify successful cases"):

            macro.replace(adds_one_node, adds_three_node)
            self.assertEqual(
                macro(one__x=0).three__result,
                5,
                msg="Result should be bigger after replacing an add_one node with an "
                    "add_three macro"
            )
            self.assertFalse(
                adds_one_node.connected,
                msg="Replaced node should get disconnected"
            )
            self.assertIsNone(
                adds_one_node.parent,
                msg="Replaced node should get orphaned"
            )

            add_one_class = macro.wrap_as.single_value_node()(add_one)
            self.assertTrue(issubclass(add_one_class, SingleValue), msg="Sanity check")
            macro.replace(adds_three_node, add_one_class)
            self.assertEqual(
                macro(one__x=0).three__result,
                3,
                msg="Should be possible to replace with a class instead of an instance"
            )

            macro.replace("two", adds_three_node)
            self.assertEqual(
                macro(one__x=0).three__result,
                5,
                msg="Should be possible to replace by label"
            )

            macro.two.replace_with(adds_one_node)
            self.assertEqual(
                macro(one__x=0).three__result,
                3,
                msg="Nodes should have syntactic sugar for invoking replacement"
            )

            @Macro.wrap_as.function_node()
            def add_two(x):
                result = x + 2
                return result
            macro.two = add_two
            self.assertEqual(
                macro(one__x=0).three__result,
                4,
                msg="Composite should allow replacement when a class is assigned"
            )

            self.assertListEqual(
                macro.starting_nodes,
                [macro.one],
                msg="Sanity check"
            )
            new_starter = add_two()
            macro.one.replace_with(new_starter)
            self.assertListEqual(
                macro.starting_nodes,
                [new_starter],
                msg="Replacement should be reflected in the starting nodes"
            )
            self.assertIs(
                macro.inputs.one__x,
                new_starter.inputs.x,
                msg="Replacement should be reflected in composite IO"
            )

        with self.subTest("Verify failure cases"):
            another_macro = Macro(add_three_macro)
            another_node = Macro(
                add_three_macro,
                inputs_map={"one__x": "x"},
                outputs_map={"three__result": "result"},
            )
            another_macro.now_its_a_child = another_node

            with self.assertRaises(
                ValueError,
                msg="Should fail when replacement has a parent"
            ):
                macro.replace(macro.two, another_node)

            another_macro.remove(another_node)
            another_node.inputs.x = another_macro.outputs.three__result
            with self.assertRaises(
                ValueError,
                msg="Should fail when replacement is connected"
            ):
                macro.replace(macro.two, another_node)

            another_node.disconnect()
            an_ok_replacement = another_macro.two
            another_macro.remove(an_ok_replacement)
            with self.assertRaises(
                ValueError,
                msg="Should fail if the node being replaced isn't a child"
            ):
                macro.replace(another_node, an_ok_replacement)

            @Macro.wrap_as.function_node()
            def add_two_incompatible_io(not_x):
                result_is_not_my_name = not_x + 2
                return result_is_not_my_name

            with self.assertRaises(
                AttributeError,
                msg="Replacing via class assignment should fail if the class has "
                    "incompatible IO"
            ):
                macro.two = add_two_incompatible_io


if __name__ == '__main__':
    unittest.main()
