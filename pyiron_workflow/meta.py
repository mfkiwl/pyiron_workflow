"""
Meta nodes are callables that create a node class instead of a node instance.
"""

from __future__ import annotations

from typing import Optional

from pyiron_workflow.function import (
    Function,
    function_node,
)
from pyiron_workflow.macro import AbstractMacro, macro_node
from pyiron_workflow.node import Node


def list_to_output(length: int, **node_class_kwargs) -> type[Function]:
    """
    A meta-node that returns a node class with :param:`length` input channels and
    maps these to a single output channel with type `list`.
    """

    def _list_to_many(length: int):
        template = f"""
def __list_to_many(input_list: list):
    {"; ".join([f"out{i} = input_list[{i}]" for i in range(length)])}
    return {", ".join([f"out{i}" for i in range(length)])}
        """
        exec(template)
        return locals()["__list_to_many"]

    return function_node(*(f"output{n}" for n in range(length)))(
        _list_to_many(length=length), **node_class_kwargs
    )


def input_to_list(length: int, **node_class_kwargs) -> type[Function]:
    """
    A meta-node that returns a node class with :param:`length` output channels and
    maps an input list to these.
    """

    def _many_to_list(length: int):
        template = f"""
def __many_to_list({", ".join([f"inp{i}=None" for i in range(length)])}):
    return [{", ".join([f"inp{i}" for i in range(length)])}]
        """
        exec(template)
        return locals()["__many_to_list"]

    return function_node("output_list")(
        _many_to_list(length=length), **node_class_kwargs
    )


def for_loop(
    loop_body_class: type[Node],
    length: int,
    iterate_on: str | tuple[str] | list[str],
    # TODO:
) -> type[AbstractMacro]:
    """
    An _extremely rough_ first draft of a for-loop meta-node.

    Takes a node class, how long the loop should be, and which input(s) of the provided
    node class should be looped over (given as strings of the channel labels) and
    builds a macro that

    - Makes copies of the provided node class, i.e. the "body node"
    - For each input channel specified to "loop over", creates a list-to-many node and
      connects each of its outputs to their respective body node inputs
    - For all other inputs, makes a 1:1 node and connects its output to _all_ of the
      body nodes
    - Relables the macro IO to match the passed node class IO so that list-ified IO
      (i.e. the specified input and all output) is all caps

    Examples:

        >>> from pyiron_workflow import Workflow
        >>>
        >>> denominators = list(range(1, 5))
        >>> bulk_loop = Workflow.create.meta.for_loop(
        ...     Workflow.create.standard.Divide,
        ...     len(denominators),
        ...     iterate_on = ("other",),
        ... )()
        >>> bulk_loop.inputs.obj = 1
        >>> bulk_loop.inputs.OTHER = denominators
        >>> bulk_loop().TRUEDIV
        [1.0, 0.5, 0.3333333333333333, 0.25]

    TODO:

        - Refactor like crazy, it's super hard to read and some stuff is too hard-coded
        - Give some sort of access to flow control??
        - How to handle passing executors to the children? Maybe this is more
          generically a Macro question?
        - Is it possible to somehow dynamically adapt the held graph depending on the
          length of the input values being iterated over? Tricky to keep IO well defined
        - Allow a different mode, or make a different meta node, that makes all possible
          pairs of body nodes given the input being looped over instead of just :param:`length`
        - Provide enter and exit magic methods so we can `for` or `with` this fancy-like
    """
    iterate_on = [iterate_on] if isinstance(iterate_on, str) else iterate_on

    def make_loop(macro):
        macro.inputs_map = {}
        macro.outputs_map = {}
        body_nodes = []

        # Parallelize over body nodes
        for n in range(length):
            body_nodes.append(
                macro.add_child(
                    loop_body_class(label=f"{loop_body_class.__name__}_{n}")
                )
            )

        # Make input interface
        for label, inp in body_nodes[0].inputs.items():
            # Don't rely on inp.label directly, since inputs may be a Composite IO
            # panel that has a different key for this input channel than its label

            # Scatter a list of inputs to each node separately
            if label in iterate_on:
                interface = list_to_output(length)(
                    parent=macro,
                    label=label.upper(),
                    input_list=[inp.default] * length,
                )
                # Connect each body node input to the input interface's respective
                # output
                for body_node, out in zip(body_nodes, interface.outputs):
                    body_node.inputs[label] = out
                macro.inputs_map[interface.inputs.input_list.scoped_label] = (
                    interface.label
                )
            # Or broadcast the same input to each node equally
            else:
                interface = macro.create.standard.UserInput(
                    label=label,
                    user_input=inp.default,
                    parent=macro,
                )
                for body_node in body_nodes:
                    body_node.inputs[label] = interface
                macro.inputs_map[interface.scoped_label] = interface.label

        # Make output interface: outputs to lists
        for label, out in body_nodes[0].outputs.items():
            interface = input_to_list(length)(
                parent=macro,
                label=label.upper(),
            )
            # Connect each body node output to the output interface's respective input
            for body_node, inp in zip(body_nodes, interface.inputs):
                inp.connect(body_node.outputs[label])
                if body_node.executor is not None:
                    raise NotImplementedError(
                        "Right now the output interface gets run after each body node,"
                        "if the body nodes can run asynchronously we need something "
                        "more clever than that!"
                    )
            macro.outputs_map[interface.scoped_label] = interface.label

    return macro_node()(make_loop)


def while_loop(
    loop_body_class: type[Node],
    condition_class: type[Function],
    internal_connection_map: dict[str, str],
    inputs_map: Optional[dict[str, str]] = None,
    outputs_map: Optional[dict[str, str]] = None,
) -> type[AbstractMacro]:
    """
    An _extremely rough_ first draft of a for-loop meta-node.

    Takes body and condition node classes and builds a macro that makes a cyclic signal
    connection between them and an "if" switch, i.e. when the body node finishes it
    runs the condtion, which runs the switch, and as long as the condition result was
    `True`, the switch loops back to run the body again.
    We additionally allow four-tuples of (input node, input channel, output node,
    output channel) labels to wire data connections inside the macro, e.g. to pass data
    from the body to the condition. This is beastly syntax, but it will suffice for now.
    Finally, you can set input and output maps as normal.

    Args:
        loop_body_class (type[pyiron_workflow.node.Node]): The class for the
            body of the while-loop.
        condition_class (type[pyiron_workflow.function.AbstractFunction]): A
            single-output function node returning a `bool` controlling the while loop
            exit condition (exits on False)
        internal_connection_map (list[tuple[str, str, str, str]]): String tuples
            giving (input node, input channel, output node, output channel) labels
            connecting channel pairs inside the macro.
        inputs_map Optional[dict[str, str]]: The inputs map as usual for a macro.
        outputs_map Optional[dict[str, str]]: The outputs map as usual for a macro.

    Examples:

        >>> from pyiron_workflow import Workflow
        >>>
        >>> AddWhile = Workflow.create.meta.while_loop(
        ...     loop_body_class=Workflow.create.standard.Add,
        ...     condition_class=Workflow.create.standard.LessThan,
        ...     internal_connection_map=[
        ...         ("Add", "add", "LessThan", "obj"),
        ...         ("Add", "add", "Add", "obj")
        ...     ],
        ...     inputs_map={
        ...         "Add__obj": "a",
        ...         "Add__other": "b",
        ...         "LessThan__other": "cap"
        ...     },
        ...     outputs_map={"Add__add": "total"}
        ... )
        >>>
        >>> wf = Workflow("do_while")
        >>> wf.add_while = AddWhile(cap=10)
        >>>
        >>> wf.inputs_map = {
        ...     "add_while__a": "a",
        ...     "add_while__b": "b"
        ... }
        >>> wf.outputs_map = {"add_while__total": "total"}
        >>>
        >>> print(f"Finally, {wf(a=1, b=2).total}")
        Finally, 11

        >>> import random
        >>>
        >>> from pyiron_workflow import Workflow
        >>>
        >>> random.seed(0)  # Set the seed so the output is consistent and doctest runs
        >>>
        >>> RandomWhile = Workflow.create.meta.while_loop(
        ...     loop_body_class=Workflow.create.standard.RandomFloat,
        ...     condition_class=Workflow.create.standard.GreaterThan,
        ...     internal_connection_map=[
        ...         ("RandomFloat", "random", "GreaterThan", "obj")
        ...     ],
        ...     inputs_map={"GreaterThan__other": "threshold"},
        ...     outputs_map={"RandomFloat__random": "capped_result"}
        ... )
        >>>
        >>> # Define workflow
        >>>
        >>> wf = Workflow("random_until_small_enough")
        >>>
        >>> ## Wire together the while loop and its condition
        >>>
        >>> wf.random_while = RandomWhile()
        >>>
        >>> ## Give convenient labels
        >>> wf.inputs_map = {"random_while__threshold": "threshold"}
        >>> wf.outputs_map = {"random_while__capped_result": "capped_result"}
        >>>
        >>> # Set a threshold and run
        >>> print(f"Finally {wf(threshold=0.3).capped_result:.3f}")
        Finally 0.259
    """

    def make_loop(macro):
        body_node = macro.add_child(loop_body_class(label=loop_body_class.__name__))
        condition_node = macro.add_child(
            condition_class(label=condition_class.__name__)
        )
        switch = macro.create.standard.If(label="switch", parent=macro)

        switch.inputs.condition = condition_node
        for out_n, out_c, in_n, in_c in internal_connection_map:
            macro.children[in_n].inputs[in_c] = macro.children[out_n].outputs[out_c]

        switch.signals.output.true >> body_node >> condition_node >> switch
        macro.starting_nodes = [body_node]

        macro.inputs_map = {} if inputs_map is None else inputs_map
        macro.outputs_map = {} if outputs_map is None else outputs_map

    return macro_node()(make_loop)
