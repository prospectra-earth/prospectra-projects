# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# DBTITLE 1,Introduction
# MAGIC %md
# MAGIC # STAC Exploration for Ribera del Duero Satellite Imagery
# MAGIC
# MAGIC This notebook introduces **STAC (SpatioTemporal Asset Catalogs)** — a standardized way to discover and access satellite imagery.
# MAGIC
# MAGIC ## What is STAC?
# MAGIC
# MAGIC STAC provides:
# MAGIC * **Catalogs**: Collections of satellite imagery organized by mission/sensor
# MAGIC * **Items**: Individual satellite scenes with metadata (date, cloud cover, location)
# MAGIC * **Assets**: The actual data files (bands/channels) you can download
# MAGIC
# MAGIC ## Our Goal
# MAGIC
# MAGIC We'll explore Sentinel-2 imagery over the **DO Ribera del Duero** wine region in Spain to understand:
# MAGIC 1. How to connect to a STAC API
# MAGIC 2. How to search for imagery by location and time
# MAGIC 3. What data is available (bands, dates, cloud cover)
# MAGIC 4. How to access the actual raster files
# MAGIC
# MAGIC This is the foundation for building our vineyard intelligence pipeline.

# COMMAND ----------

# DBTITLE 1,Install STAC client
# Install pystac-client - the Python library for querying STAC APIs
%run ./00_setup

# COMMAND ----------

# DBTITLE 1,Connect to STAC catalog
from pystac_client import Client
import json

# Connect to AWS Earth Search - a free, public STAC API for satellite imagery
# No authentication required, no throttling limits
STAC_API_URL = "https://earth-search.aws.element84.com/v1"

catalog = Client.open(STAC_API_URL)

print(f"Connected to: {catalog.title}")
print(f"Description: {catalog.description}")
print(f"\nAvailable collections:")

# List all available satellite data collections
for collection in catalog.get_collections():
    print(f"  - {collection.id}: {collection.title}")

collection

# COMMAND ----------

# DBTITLE 1,Define Ribera del Duero region
# Define a bounding box around the DO Ribera del Duero wine region
# Format: [west_longitude, south_latitude, east_longitude, north_latitude]

ribera_bbox = [-4.5, 41.4, -3.2, 41.9]

print("Ribera del Duero bounding box:")
print(f"  West:  {ribera_bbox[0]}° (longitude)")
print(f"  South: {ribera_bbox[1]}° (latitude)")
print(f"  East:  {ribera_bbox[2]}° (longitude)")
print(f"  North: {ribera_bbox[3]}° (latitude)")
print(f"\nThis covers approximately {(ribera_bbox[2] - ribera_bbox[0]) * 111:.0f} km × {(ribera_bbox[3] - ribera_bbox[1]) * 111:.0f} km")
print("The region contains ~27,468 hectares of vineyards according to SIGPAC registry.")

# COMMAND ----------

import leafmap
import json

# Create a map centered on Ribera del Duero
m = leafmap.Map(center=[(ribera_bbox[1] + ribera_bbox[3]) / 2, (ribera_bbox[0] + ribera_bbox[2]) / 2], zoom=10)

# Create a GeoJSON rectangle for the bounding box
bbox_geojson = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [ribera_bbox[0], ribera_bbox[1]],  # SW corner
            [ribera_bbox[2], ribera_bbox[1]],  # SE corner
            [ribera_bbox[2], ribera_bbox[3]],  # NE corner
            [ribera_bbox[0], ribera_bbox[3]],  # NW corner
            [ribera_bbox[0], ribera_bbox[1]]   # Close the polygon
        ]]
    },
    "properties": {"name": "Ribera del Duero"}
}

# Add the bounding box to the map
m.add_geojson(bbox_geojson, layer_name="Ribera del Duero Region", style={"color": "red", "fillOpacity": 0.2})

# Display the map
display(m)

# COMMAND ----------

# DBTITLE 1,Search for Sentinel-2 imagery
from datetime import datetime

# Search for Sentinel-2 Level 2A (atmospherically corrected) imagery
# Limited to July 2024 for initial exploration

search = catalog.search(
    collections=["sentinel-2-l2a"],  # Sentinel-2 L2A = surface reflectance (atmospherically corrected)
    bbox=ribera_bbox,
    datetime="2024-07-01/2024-07-31",  # One month for exploration
    max_items=100  # Limit results for exploration
)

# Execute the search and collect all items
items = list(search.items())

print(f"Found {len(items)} Sentinel-2 scenes over Ribera del Duero in July 2024")
print(f"\nEach 'item' represents one satellite overpass with imagery.")
print(f"Sentinel-2 has a 5-day revisit frequency, so we expect ~6 overpasses per month.")

items

# COMMAND ----------

# DBTITLE 1,Explore a single item
# Let's examine the first item in detail
if items:
    item = items[0]
    
    print("=" * 80)
    print("ITEM METADATA")
    print("=" * 80)
    print(f"ID: {item.id}")
    print(f"Date: {item.datetime}")
    print(f"Cloud Cover: {item.properties.get('eo:cloud_cover', 'N/A')}%")
    print(f"Geometry: {item.geometry['type']} with {len(item.geometry['coordinates'][0])} vertices")
    
    print("\n" + "=" * 80)
    print("AVAILABLE ASSETS (Bands & Metadata)")
    print("=" * 80)
    
    # List all available assets (spectral bands + metadata files)
    for asset_key, asset in item.assets.items():
        print(f"\n{asset_key}:")
        print(f"  Title: {asset.title}")
        print(f"  Type: {asset.media_type}")
        print(f"  URL: {asset.href[:80]}...")
        
    print("\n" + "=" * 80)
    print("KEY BANDS FOR VEGETATION ANALYSIS")
    print("=" * 80)
    print("B02 (Blue):   490 nm - 10m resolution")
    print("B03 (Green):  560 nm - 10m resolution")
    print("B04 (Red):    665 nm - 10m resolution")
    print("B08 (NIR):    842 nm - 10m resolution - for NDVI")
    print("B11 (SWIR1): 1610 nm - 20m resolution - for NDMI (water stress)")
    print("B12 (SWIR2): 2190 nm - 20m resolution")
else:
    print("No items found!")

# COMMAND ----------

# DBTITLE 1,Visualize temporal coverage
import pandas as pd
from shapely.geometry import shape

# Extract key metadata from all items into a DataFrame
if items:
    data = []
    for item in items:
        # Get the geometry and calculate centroid
        geom = shape(item.geometry)
        centroid = geom.centroid
        
        data.append({
            'date': item.datetime.date() if item.datetime else None,
            'scene_id': item.id,
            'cloud_cover': item.properties.get('eo:cloud_cover', None),
            'platform': item.properties.get('platform', 'N/A'),
            'processing_level': item.properties.get('processing:level', 'N/A'),
            'centroid_lon': centroid.x,
            'centroid_lat': centroid.y,
            'geometry': item.geometry  # Full footprint geometry as GeoJSON
        })
    
    df = pd.DataFrame(data)
    df = df.sort_values('date')
    
    print("Temporal Coverage - July 2024")
    print("=" * 80)
    display(df)
    
    print(f"\nSummary:")
    print(f"  Total scenes: {len(df)}")
    print(f"  Date range: {df['date'].min()} to {df['date'].max()}")
    print(f"  Average cloud cover: {df['cloud_cover'].mean():.1f}%")
    print(f"  Best scene (lowest clouds): {df.loc[df['cloud_cover'].idxmin(), 'date']} ({df['cloud_cover'].min():.1f}% clouds)")
    
    print("\n💡 Next Steps:")
    print("   - Expand search to full 7 seasons (2019-2025)")
    print("   - Filter scenes by cloud cover threshold (< 20%)")
    print("   - Download and process bands to calculate NDVI, NDRE, NDMI")
    print("   - Aggregate to H3 hexagonal cells for spatial analysis")
else:
    print("No items to visualize!")

item

# COMMAND ----------

import rasterio
import numpy as np
import matplotlib.pyplot as plt

# Select the first item (scene)
if items:
    item = items[0]
    # Get URLs for Red, Green, Blue bands (using common names from STAC)
    red_url = item.assets['red'].href
    green_url = item.assets['green'].href
    blue_url = item.assets['blue'].href

    # Read bands using rasterio
    with rasterio.open(red_url) as red_src, \
         rasterio.open(green_url) as green_src, \
         rasterio.open(blue_url) as blue_src:
        red = red_src.read(1)
        green = green_src.read(1)
        blue = blue_src.read(1)

    # Stack bands into RGB image
    rgb = np.stack([red, green, blue], axis=-1)

    # Normalize for display (min-max scaling)
    rgb_min = np.percentile(rgb, 2)
    rgb_max = np.percentile(rgb, 98)
    rgb_norm = np.clip((rgb - rgb_min) / (rgb_max - rgb_min), 0, 1)

    # Plot RGB composite
    plt.figure(figsize=(8, 8))
    plt.imshow(rgb_norm)
    plt.title(f"Sentinel-2 RGB Composite\nScene: {item.id}\nDate: {item.datetime.date()}")
    plt.axis('off')
    plt.show()
else:
    print("No items found!")

# COMMAND ----------

