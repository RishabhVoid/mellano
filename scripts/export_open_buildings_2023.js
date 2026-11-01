// Optional Earth Engine Code Editor export, if public GCS downloads are unsuitable.
// Requires your own Earth Engine login/project. Does not create flood data.
// Download the completed Drive files into the documented raw/derived paths.
var aoi = ee.Geometry.Rectangle([77.20, 28.58, 77.32, 28.72], null, false);
var footprints = ee.FeatureCollection('GOOGLE/Research/open-buildings/v3/polygons')
    .filterBounds(aoi).filter(ee.Filter.gte('confidence', 0.75));
var annual = ee.ImageCollection('GOOGLE/Research/open-buildings-temporal/v1')
    .filterBounds(aoi).filterDate('2023-01-01', '2024-01-01').mosaic();
var height = annual.select('building_height')
    .updateMask(annual.select('building_presence').gte(0.5));
Export.image.toDrive({image: height, description: 'open_buildings_height_2023',
    fileNamePrefix: 'open_buildings_height_2023', region: aoi, crs: 'EPSG:32643',
    scale: 2, maxPixels: 1e9, fileFormat: 'GeoTIFF', formatOptions: {cloudOptimized: true}});
// Medians are recomputed locally for BOTH OSM and Google footprints after de-duplication.
Export.table.toDrive({collection: footprints, description: 'open_buildings_v3_aoi',
    fileNamePrefix: 'open_buildings_v3_aoi', fileFormat: 'GeoJSON'});
Map.centerObject(aoi, 12);
Map.addLayer(height, {min: 0, max: 30}, '2023 building_height (metres above ground)');
