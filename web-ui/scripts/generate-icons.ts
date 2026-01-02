import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'
import sharp from 'sharp'

const ICONS_DIR = join(import.meta.dirname, '../public/icons')
const SVG_PATH = join(import.meta.dirname, '../public/gluon.svg')

// Icon sizes needed for PWA
const sizes = [
  { size: 72, name: 'icon-72.png' },
  { size: 96, name: 'icon-96.png' },
  { size: 128, name: 'icon-128.png' },
  { size: 144, name: 'icon-144.png' },
  { size: 152, name: 'icon-152.png' },
  { size: 180, name: 'apple-touch-icon.png' }, // iOS
  { size: 192, name: 'icon-192.png' },
  { size: 384, name: 'icon-384.png' },
  { size: 512, name: 'icon-512.png' },
]

// Maskable icon with padding (safe zone is 80% of icon)
const maskableSizes = [
  { size: 192, name: 'icon-192-maskable.png' },
  { size: 512, name: 'icon-512-maskable.png' },
]

async function generateIcons() {
  await mkdir(ICONS_DIR, { recursive: true })

  // Generate standard icons
  for (const { size, name } of sizes) {
    await sharp(SVG_PATH).resize(size, size).png().toFile(join(ICONS_DIR, name))
    console.log(`Generated ${name}`)
  }

  // Generate maskable icons with padding for safe zone
  // Maskable icons need 10% padding on each side (20% total)
  for (const { size, name } of maskableSizes) {
    const innerSize = Math.round(size * 0.8) // 80% for content
    const padding = Math.round(size * 0.1) // 10% padding each side

    // Create the inner icon
    const innerIcon = await sharp(SVG_PATH).resize(innerSize, innerSize).toBuffer()

    // Composite onto background with padding
    await sharp({
      create: {
        width: size,
        height: size,
        channels: 4,
        background: { r: 12, g: 12, b: 12, alpha: 1 }, // #0c0c0c
      },
    })
      .composite([
        {
          input: innerIcon,
          top: padding,
          left: padding,
        },
      ])
      .png()
      .toFile(join(ICONS_DIR, name))
    console.log(`Generated ${name} (maskable)`)
  }

  console.log('\nAll icons generated in public/icons/')
}

generateIcons().catch(console.error)
