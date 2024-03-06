from __future__ import annotations

from abc import ABC
from typing import Optional


class Semantics:
    """
    A component for describing the linear semantic relationship between objects.
    """

    delimiter = "/"

    def __init__(
        self, owner: HasSemantics, label: str, parent: Optional[HasSemantics] = None
    ):
        self._owner = owner
        self._label = None
        self._parent = None
        self.label = label
        self.parent = parent

    @property
    def label(self) -> str:
        return self._label

    @label.setter
    def label(self, new_label: str) -> None:
        if not isinstance(new_label, str):
            raise TypeError(f"Expected a string label but got {new_label}")
        if self.delimiter in new_label:
            raise ValueError(f"{self.delimiter} cannot be in the label")
        self._label = new_label

    @property
    def parent(self) -> HasSemantics | None:
        return self._parent

    @parent.setter
    def parent(self, new_parent: HasSemantics | None) -> None:
        if new_parent is not None:
            if isinstance(self._owner, Parentmost):
                raise TypeError(
                    f"{self.label} is {Parentmost.__name__}, and cannot receive a "
                    f"parent"
                )
            if not isinstance(new_parent, HasSemantics):
                raise ValueError(
                    f"Expected None or another {HasSemantics.__name__} for the "
                    f"semantic parent of {self.label}, but got {new_parent}"
                )
        self._parent = new_parent

    @property
    def path(self) -> str:
        """
        The path of node labels from the graph root (parent-most node) down to this
        node.
        """
        prefix = "" if self.parent is None else self.parent.semantics.path
        return prefix + self.delimiter + self.label

    @property
    def root(self) -> HasSemantics:
        """The parent-most object in this semantic path; may be self."""
        return self._owner if self.parent is None else self.parent.semantics.root

    def __getstate__(self):
        state = dict(self.__dict__)
        state["_parent"] = None
        # Comment on moving this to semantics)
        # Basically we want to avoid recursion during (de)serialization; when the
        # parent object is deserializing itself, _it_ should know who its children are
        # and inform them of this.
        #
        # Original comment when this behaviour belonged to node)
        # I am not at all confident that removing the parent here is the _right_
        # solution.
        # In order to run composites on a parallel process, we ship off just the nodes
        # and starting nodes.
        # When the parallel process returns these, they're obviously different
        # instances, so we re-parent them back to the receiving composite.
        # At the same time, we want to make sure that the _old_ children get orphaned.
        # Of course, we could do that directly in the composite method, but it also
        # works to do it here.
        # Something I like about this, is it also means that when we ship groups of
        # nodes off to another process with cloudpickle, they're definitely not lugging
        # along their parent, its connections, etc. with them!
        # This is all working nicely as demonstrated over in the macro test suite.
        # However, I have a bit of concern that when we start thinking about
        # serialization for storage instead of serialization to another process, this
        # might introduce a hard-to-track-down bug.
        # For now, it works and I'm going to be super pragmatic and go for it, but
        # for the record I am admitting that the current shallowness of my understanding
        # may cause me/us headaches in the future.
        # -Liam
        state["_owner"] = None
        # We do the same thing for "owner", but only because h5io can't handle any
        # recursion; this object and its owner always come together so there's no
        # efficiency reason for this, it's just to accommodate the backend tool...
        return state

    def __setstate__(self, state):
        self.__dict__.update(**state)


class HasSemantics(ABC):
    """A mixin for classes with a semantic component."""
    def __init__(
        self,
        semantic_label,
        *args,
        semantic_parent: Optional[HasSemantics] = None,
        **kwargs
    ):
        self._semantics = Semantics(self, semantic_label, semantic_parent)
        super().__init__(*args, **kwargs)

    @property
    def semantics(self) -> Semantics:
        return self._semantics

    def __setstate__(self, state):
        self.__dict__.update(state)

        self.semantics._owner = self
        # Re-set self to accommodate h5io's recursion aversion


class Parentmost(HasSemantics, ABC):
    """Has semantics, but will not accept a parent assignment"""
