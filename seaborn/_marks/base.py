from __future__ import annotations
from dataclasses import dataclass, fields, field

import numpy as np
import pandas as pd
import matplotlib as mpl

from seaborn._core.properties import PROPERTIES, Property

from typing import Any, Callable, Union
from collections.abc import Generator
from numpy import ndarray
from pandas import DataFrame
from matplotlib.artist import Artist
from seaborn._core.properties import RGBATuple, DashPattern, DashPatternWithOffset
from seaborn._core.scales import Scale


class Mappable:
    def __init__(
        self,
        val: Any = None,
        depend: str | None = None,
        rc: str | None = None,
        grouping: bool = True,
    ):
        """
        Property that can be mapped from data or set directly, with flexible defaults.

        Parameters
        ----------
        val : Any
            Use this value as the default.
        depend : str
            Use the value of this feature as the default.
        rc : str
            Use the value of this rcParam as the default.
        grouping : bool
            If True, use the mapped variable to define groups.

        """
        if depend is not None:
            assert depend in PROPERTIES
        if rc is not None:
            assert rc in mpl.rcParams

        self._val = val
        self._rc = rc
        self._depend = depend
        self._grouping = grouping

    def __repr__(self):
        """Nice formatting for when object appears in Mark init signature."""
        if self._val is not None:
            s = f"<{repr(self._val)}>"
        elif self._depend is not None:
            s = f"<depend:{self._depend}>"
        elif self._rc is not None:
            s = f"<rc:{self._rc}>"
        else:
            s = "<undefined>"
        return s

    @property
    def depend(self) -> Any:
        """Return the name of the feature to source a default value from."""
        return self._depend

    @property
    def grouping(self) -> bool:
        return self._grouping

    @property
    def default(self) -> Any:
        """Get the default value for this feature, or access the relevant rcParam."""
        if self._val is not None:
            return self._val
        return mpl.rcParams.get(self._rc)


# TODO where is the right place to put this kind of type aliasing?

MappableBool = Union[bool, Mappable]
MappableString = Union[str, Mappable]
MappableFloat = Union[float, Mappable]
MappableColor = Union[str, tuple, Mappable]
MappableStyle = Union[str, DashPattern, DashPatternWithOffset, Mappable]


@dataclass
class Mark:

    artist_kws: dict = field(default_factory=dict)

    @property
    def _mappable_props(self):
        return {
            f.name: getattr(self, f.name) for f in fields(self)
            if isinstance(f.default, Mappable)
        }

    @property
    def _grouping_props(self):
        # TODO does it make sense to have variation within a Mark's
        # properties about whether they are grouping?
        return [
            f.name for f in fields(self)
            if isinstance(f.default, Mappable) and f.default.grouping
        ]

    # TODO make this method private? Would extender every need to call directly?
    def _resolve(
        self,
        data: DataFrame | dict[str, Any],
        name: str,
        scales: dict[str, Scale] | None = None,
    ) -> Any:
        """Obtain default, specified, or mapped value for a named feature.

        Parameters
        ----------
        data : DataFrame or dict with scalar values
            Container with data values for features that will be semantically mapped.
        name : string
            Identity of the feature / semantic.
        scales: dict
            Mapping from variable to corresponding scale object.

        Returns
        -------
        value or array of values
            Outer return type depends on whether `data` is a dict (implying that
            we want a single value) or DataFrame (implying that we want an array
            of values with matching length).

        """
        feature = self._mappable_props[name]
        prop = PROPERTIES.get(name, Property(name))
        directly_specified = not isinstance(feature, Mappable)
        return_multiple = isinstance(data, pd.DataFrame)
        return_array = return_multiple and not name.endswith("style")

        # Special case width because it needs to be resolved and added to the dataframe
        # during layer prep (so the Move operations use it properly).
        # TODO how does width *scaling* work, e.g. for violin width by count?
        if name == "width":
            directly_specified = directly_specified and name not in data

        if directly_specified:
            feature = prop.standardize(feature)
            if return_multiple:
                feature = [feature] * len(data)
            if return_array:
                feature = np.array(feature)
            return feature

        if name in data:
            if scales is None or name not in scales:
                # TODO Might this obviate the identity scale? Just don't add a scale?
                feature = data[name]
            else:
                feature = scales[name](data[name])
            if return_array:
                feature = np.asarray(feature)
            return feature

        if feature.depend is not None:
            # TODO add source_func or similar to transform the source value?
            # e.g. set linewidth as a proportion of pointsize?
            return self._resolve(data, feature.depend, scales)

        default = prop.standardize(feature.default)
        if return_multiple:
            default = [default] * len(data)
        if return_array:
            default = np.array(default)
        return default

    def _infer_orient(self, scales: dict) -> str:  # TODO type scales

        # TODO The original version of this (in seaborn._oldcore) did more checking.
        # Paring that down here for the prototype to see what restrictions make sense.

        # TODO rethink this to map from scale type to "DV priority" and use that?
        # e.g. Nominal > Discrete > Continuous

        x_type = None if "x" not in scales else scales["x"].scale_type
        y_type = None if "y" not in scales else scales["y"].scale_type

        if x_type is None or x_type == "computed":
            return "y"

        elif y_type is None or y_type == "computed":
            return "x"

        elif x_type != "nominal" and y_type == "nominal":
            return "y"

        elif x_type != "continuous" and y_type == "continuous":

            # TODO should we try to orient based on number of unique values?

            return "x"

        elif x_type == "continuous" and y_type != "continuous":
            return "y"

        else:
            return "x"

    def _plot(
        self,
        split_generator: Callable[[], Generator],
        scales: dict[str, Scale],
        orient: str,
    ) -> None:
        """Main interface for creating a plot."""
        raise NotImplementedError()

    def _legend_artist(
        self, variables: list[str], value: Any, scales: dict[str, Scale],
    ) -> Artist:
        # TODO return some sensible default?
        raise NotImplementedError


def resolve_properties(
    mark: Mark, data: DataFrame, scales: dict[str, Scale]
) -> dict[str, Any]:

    props = {
        name: mark._resolve(data, name, scales) for name in mark._mappable_props
    }
    return props


def resolve_color(
    mark: Mark,
    data: DataFrame | dict,
    prefix: str = "",
    scales: dict[str, Scale] | None = None,
) -> RGBATuple | ndarray:
    """
    Obtain a default, specified, or mapped value for a color feature.

    This method exists separately to support the relationship between a
    color and its corresponding alpha. We want to respect alpha values that
    are passed in specified (or mapped) color values but also make use of a
    separate `alpha` variable, which can be mapped. This approach may also
    be extended to support mapping of specific color channels (i.e.
    luminance, chroma) in the future.

    Parameters
    ----------
    mark :
        Mark with the color property.
    data :
        Container with data values for features that will be semantically mapped.
    prefix :
        Support "color", "fillcolor", etc.

    """
    color = mark._resolve(data, f"{prefix}color", scales)
    alpha = mark._resolve(data, f"{prefix}alpha", scales)

    def visible(x, axis=None):
        """Detect "invisible" colors to set alpha appropriately."""
        # TODO First clause only needed to handle non-rgba arrays,
        # which we are trying to handle upstream
        return np.array(x).dtype.kind != "f" or np.isfinite(x).all(axis)

    # Second check here catches vectors of strings with identity scale
    # It could probably be handled better upstream. This is a tricky problem
    if np.ndim(color) < 2 and all(isinstance(x, float) for x in color):
        if len(color) == 4:
            return mpl.colors.to_rgba(color)
        alpha = alpha if visible(color) else np.nan
        return mpl.colors.to_rgba(color, alpha)
    else:
        if np.ndim(color) == 2 and color.shape[1] == 4:
            return mpl.colors.to_rgba_array(color)
        alpha = np.where(visible(color, axis=1), alpha, np.nan)
        return mpl.colors.to_rgba_array(color, alpha)

    # TODO should we be implementing fill here too?
    # (i.e. set fillalpha to 0 when fill=False)


class MultiMark(Mark):

    # TODO implement this as a way to wrap multiple marks (e.g. line and ribbon)
    # It should be fairly lightweight, the main thing is to expose the union
    # of each mark's parameters and then to call them sequentially in _plot.
    pass
