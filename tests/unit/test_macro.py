from concurrent.futures import Future
from functools import partialmethod
from sys import version_info
from time import sleep
import unittest

from pyiron_workflow.channels import NotData
from pyiron_workflow.function import SingleValue
from pyiron_workflow.macro import Macro
from pyiron_workflow.topology import CircularDataFlowError


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
                macro.inputs.one__x.value_receiver,
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

    def test_macro_connections_after_replace(self):
        # If the macro-level IO is going to change after replacing a child,
        # it had better still be able to recreate all the macro-level IO connections
        # For macro IO channels that weren't connected, we don't really care
        # If it fails to replace, it had better revert to its original state

        macro = Macro(add_three_macro, one__x=0)
        downstream = SingleValue(add_one, x=macro.outputs.three__result)
        downstream.pull()
        self.assertEqual(
            0 + (1 + 1 + 1) + 1,
            downstream.outputs.result.value,
            msg="Sanity check that our test setup is what we want: macro->single"
        )

        def add_two(x):
            result = x + 2
            return result
        compatible_replacement = SingleValue(add_two)

        macro.replace(macro.three, compatible_replacement)
        downstream.pull()
        self.assertEqual(
            len(downstream.inputs.x.connections),
            1,
            msg="After replacement, the downstream node should still have exactly one "
                "connection to the macro"
        )
        self.assertIs(
            downstream.inputs.x.connections[0],
            macro.outputs.three__result,
            msg="The one connection should be the living, updated macro IO channel"
        )
        self.assertEqual(
            0 + (1 + 1 + 2) + 1,
            downstream.outputs.result.value,
            msg="The whole flow should still function after replacement, but with the "
                "new behaviour (and extra 1 added)"
        )

        def different_signature(x):
            # When replacing the final node of add_three_macro, the rebuilt IO will
            # no longer have three__result, but rather three__changed_output_label,
            # which will break existing macro-level IO if the macro output is connected
            changed_output_label = x + 3
            return changed_output_label

        incompatible_replacement = SingleValue(
            different_signature,
            label="original_label"
        )
        with self.assertRaises(
            AttributeError,
            msg="macro.three__result is connected output, but can't be found in the "
                "rebuilt IO, so an exception is expected"
        ):
            macro.replace(macro.three, incompatible_replacement)
        self.assertIs(
            macro.three,
            compatible_replacement,
            msg="Failed replacements should get reverted, putting the original node "
                "back"
        )
        self.assertIs(
            macro.three.outputs.result.value_receiver,
            macro.outputs.three__result,
            msg="Failed replacements should get reverted, restoring the link between "
                "child IO and macro IO"
        )
        self.assertIs(
            downstream.inputs.x.connections[0],
            macro.outputs.three__result,
            msg="Failed replacements should get reverted, and macro IO should be as "
                "it was before"
        )
        self.assertFalse(
            incompatible_replacement.connected,
            msg="Failed replacements should get reverted, leaving the replacement in "
                "its original state"
        )
        self.assertEqual(
            "original_label",
            incompatible_replacement.label,
            msg="Failed replacements should get reverted, leaving the replacement in "
                "its original state"
        )
        macro > downstream
        # If we want to push, we need to define a connection formally
        macro.run(one__x=1)
        # Fresh input to make sure updates are actually going through
        self.assertEqual(
            1 + (1 + 1 + 2) + 1,
            downstream.outputs.result.value,
            msg="Final integration test that replacements get reverted, the macro "
                "function and downstream results should be the same as before"
        )

        downstream.disconnect()
        macro.replace(macro.three, incompatible_replacement)
        self.assertIs(
            macro.three,
            incompatible_replacement,
            msg="Since it is only incompatible with the external connections and we "
                "broke those first, replacement is expected to work fine now"
        )
        macro(one__x=2)
        self.assertEqual(
            2 + (1 + 1 + 3),
            macro.outputs.three__changed_output_label.value,
            msg="For all to be working, we need the result with the new behaviour "
                "at its new location"
        )

    def test_with_executor(self):
        macro = Macro(add_three_macro)
        downstream = SingleValue(add_one, x=macro.outputs.three__result)
        macro > downstream  # Manually specify since we'll run the macro but look
        # at the downstream output, and none of this is happening in a workflow

        original_one = macro.one
        macro.executor = True

        self.assertIs(
            NotData,
            macro.outputs.three__result.value,
            msg="Sanity check that test is in right starting condition"
        )

        result = macro.run(one__x=0)
        self.assertIsInstance(
            result,
            Future,
            msg="Should be running as a parallel process"
        )
        self.assertIs(
            NotData,
            downstream.outputs.result.value,
            msg="Downstream events should not yet have triggered either, we should wait"
                "for the callback when the result is ready"
        )

        returned_nodes = result.result()  # Wait for the process to finish
        self.assertIsNot(
            original_one,
            returned_nodes.one,
            msg="Executing in a parallel process should be returning new instances"
        )
        # self.assertIs(
        #     returned_nodes.one,
        #     macro.nodes.one,
        #     msg="Returned nodes should be taken as children"
        # )  # You can't do this, result.result() is returning new instances each call
        self.assertIs(
            macro,
            macro.nodes.one.parent,
            msg="Returned nodes should get the macro as their parent"
            # Once upon a time there was some evidence that this test was failing
            # stochastically, but I just ran the whole test suite 6 times and this test
            # 8 times and it always passed fine, so maybe the issue is resolved...
        )
        self.assertIsNone(
            original_one.parent,
            msg="Original nodes should be orphaned"
            # Note: At time of writing, this is accomplished in Node.__getstate__,
            #       which feels a bit dangerous...
        )
        self.assertEqual(
            0 + 3,
            macro.outputs.three__result.value,
            msg="And of course we expect the calculation to actually run"
        )
        self.assertIs(
            downstream.inputs.x.connections[0],
            macro.outputs.three__result,
            msg="The macro should still be connected to "
        )
        sleep(0.2)  # Give a moment for the ran signal to emit and downstream to run
        # I'm a bit surprised this sleep is necessary
        self.assertEqual(
            0 + 3 + 1,
            downstream.outputs.result.value,
            msg="The finishing callback should also fire off the ran signal triggering"
                "downstream execution"
        )

    def test_pulling_from_inside_a_macro(self):
        upstream = SingleValue(add_one, x=2)
        macro = Macro(add_three_macro, one__x=upstream)
        macro.inputs.one__x = 0  # Set value
        # Now macro.one.inputs.x has both value and a connection

        print("MACRO ONE INPUT X", macro.one.inputs.x.value, macro.one.inputs.x.connections)

        self.assertEqual(
            0 + 1 + 1,
            macro.two.pull(run_parent_trees_too=False),
            msg="Without running parent trees, the pulling should only run upstream "
                "nodes _inside_ the scope of the macro, relying on the explicit input"
                "value"
        )

        self.assertEqual(
            (2 + 1) + 1 + 1,
            macro.two.pull(run_parent_trees_too=True),
            msg="Running with parent trees, the pulling should also run the parents "
                "data dependencies first"
        )

    def test_recovery_after_failed_pull(self):
        def grab_x_and_run(node):
            """Grab a couple connections from an add_one-like node"""
            return node.inputs.x.connections + node.signals.input.run.connections

        with self.subTest("When the local scope has cyclic data flow"):
            def cyclic_macro(macro):
                macro.one = SingleValue(add_one)
                macro.two = SingleValue(add_one, x=macro.one)
                macro.one.inputs.x = macro.two
                macro.one > macro.two
                macro.starting_nodes = [macro.one]
                # We need to manually specify execution since the data flow is cyclic

            m = Macro(cyclic_macro)

            initial_labels = list(m.nodes.keys())

            def grab_connections(macro):
                return grab_x_and_run(macro.one) + grab_x_and_run(macro.two)

            initial_connections = grab_connections(m)

            with self.assertRaises(
                CircularDataFlowError,
                msg="Pull should only work for DAG workflows"
            ):
                m.two.pull()
            self.assertListEqual(
                initial_labels,
                list(m.nodes.keys()),
                msg="Labels should be restored after failing to pull because of acyclicity"
            )
            self.assertTrue(
                all(c is ic for (c, ic) in zip(grab_connections(m), initial_connections)),
                msg="Connections should be restored after failing to pull because of "
                    "cyclic data flow"
            )

        with self.subTest("When the parent scope has cyclic data flow"):
            n1 = SingleValue(add_one, label="n1", x=0)
            n2 = SingleValue(add_one, label="n2", x=n1)
            m = Macro(add_three_macro, label="m", one__x=n2)

            self.assertEqual(
                0 + 1 + 1 + (1 + 1 + 1),
                m.three.pull(run_parent_trees_too=True),
                msg="Sanity check, without cyclic data flows pulling here should be ok"
            )

            n1.inputs.x = n2

            initial_connections = grab_x_and_run(n1) + grab_x_and_run(n2)
            with self.assertRaises(
                CircularDataFlowError,
                msg="Once the outer scope has circular data flows, pulling should fail"
            ):
                m.three.pull(run_parent_trees_too=True)
            self.assertTrue(
                all(
                    c is ic
                    for (c, ic) in zip(
                        grab_x_and_run(n1) + grab_x_and_run(n2), initial_connections
                    )
                ),
                msg="Connections should be restored after failing to pull because of "
                    "cyclic data flow in the outer scope"
            )
            self.assertEqual(
                "n1",
                n1.label,
                msg="Labels should get restored in the outer scope"
            )
            self.assertEqual(
                "one",
                m.one.label,
                msg="Labels should not have even gotten perturbed to start with in the"
                    "inner scope"
            )

        with self.subTest("When a node breaks upstream"):
            def fail_at_zero(x):
                y = 1 / x
                return y

            n1 = SingleValue(fail_at_zero, x=0)
            n2 = SingleValue(add_one, x=n1, label="n1")
            n_not_used = SingleValue(add_one)
            n_not_used > n2  # Just here to make sure it gets restored

            with self.assertRaises(
                ZeroDivisionError,
                msg="The underlying error should get raised"
            ):
                n2.pull()
            self.assertEqual(
                "n1",
                n2.label,
                msg="Original labels should get restored on upstream failure"
            )
            self.assertIs(
                n_not_used,
                n2.signals.input.run.connections[0].node,
                msg="Original connections should get restored on upstream failure"
            )


if __name__ == '__main__':
    unittest.main()
