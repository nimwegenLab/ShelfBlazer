"""The look of the record types that came with the first ShelfBlazer instance (the lab's LabCollector import).

A type definition chooses its own icon ("icon"), colour ("color") and menu position (Types admin page, config/type_order.yaml).
This table only fills in for those types when their definition does not say, so instances made before these settings existed keep
their look. Nothing else in the code knows type names; a new instance can ignore this file.
"""
ICONS = {"strain": "bacterium", "plasmid": "dna", "primer": "bullseye", "chemical": "flask",
         "plate": "table-cells", "master": "layer-group", "document": "file-lines"}
COLORS = {"strain": "blue", "plasmid": "green", "primer": "orange", "chemical": "pink",
          "plate": "purple", "master": "teal", "document": "red"}
ORDER = ("strain", "plasmid", "primer", "chemical", "plate", "master", "document")
