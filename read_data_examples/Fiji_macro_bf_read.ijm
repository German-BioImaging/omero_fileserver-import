// See commands for BF PLugins->Bio-Formats->Bio-Formats Macro Extensions

run("Bio-Formats Macro Extensions");

// Need to add a way to read the image import clientPath.
Ext.setId("Z:\\OMERO_in-place_import\\CAi\\Anna_Hamacher_ansla100\\AC_00054_IFRS1_mature_treatment\\hs\\56515f99-a36a-43ae-8749-6d8292a0ff37\\images\\index.xml");

Ext.getSeriesCount(seriesCount);
print("Series count:", seriesCount);

Ext.setSeries(50);

Ext.getSizeX(sizeX);
Ext.getSizeX(sizeY);
Ext.getSizeX(sizeZ);
Ext.getSizeX(sizeC);
Ext.getSizeX(sizeT);
print("X:",sizeX, "Y:", sizeY, "Z:",sizeZ, "C:", sizeC, "T:", sizeT);

Ext.getDimensionOrder(dimOrder);
print("Dim order", dimOrder);
z=0;
c=1;
t=0;
Ext.getIndex(z, c, t, index);
print("Image index", index);

Ext.openImage("Name", index);

Ext.close();