"""Vectorized 2D projection for the AOI footprint and viewer export contracts."""
from shapely import transform as transform_coordinates


def transform_xy(function, geometry):
    """Preserve geometry structure while applying a callable(x_array, y_array)."""
    return transform_coordinates(geometry, function, interleaved=False, include_z=False)
