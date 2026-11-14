# Resources Directory

This directory should contain:
- icon.png (256x256)
- icon.ico (Windows icon)
- icon.icns (macOS icon)

For now, Electron will use the default icon.

To add your own icon:
1. Create a 256x256 PNG icon
2. Convert to .ico using online tools or ImageMagick
3. Place both files in this directory
4. Update package.json build.win.icon to point to your icon.ico
