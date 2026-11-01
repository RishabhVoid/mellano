// Optional fallback only: run in the Earth Engine Code Editor with your login/project.
// Public Bristol download is preferred: uv run python -m sim.fabdem download.
// Download this GeoTIFF to data/raw/N28E077_FABDEM_V1-2.tif.
// Then run: uv run python -m sim.fabdem switch && make export.
// Verify the SAT-IO collection's version before naming/exporting it as V1-2.
// FABDEM V1-2: Neal & Hawker (2023), University of Bristol, CC BY-NC-SA 4.0.
var tile = ee.Geometry.Rectangle([77, 28, 78, 29], null, false);
var fabdem = ee.ImageCollection('projects/sat-io/open-datasets/FABDEM')
    .filterBounds(tile).mosaic();
print('Source images and version metadata — verify before export',
    ee.ImageCollection('projects/sat-io/open-datasets/FABDEM').filterBounds(tile));
Export.image.toDrive({image: fabdem, description: 'N28E077_FABDEM',
    fileNamePrefix: 'N28E077_FABDEM', region: tile, crs: 'EPSG:4326',
    crsTransform: [1/3600, 0, 77-1/7200, 0, -1/3600, 29+1/7200],
    maxPixels: 2e7, fileFormat: 'GeoTIFF', formatOptions: {cloudOptimized: true}});
Map.centerObject(tile, 10);
Map.addLayer(fabdem, {min: 180, max: 300}, 'FABDEM ground');
