import { type ClassValue, clsx } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// Extend tailwind-merge to recognize our custom typography classes
// This prevents text-body from being removed when combined with text-[color]
const customTwMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      // Add our custom typography classes to their own group
      // so they don't conflict with text color classes
      'font-size': [
        'text-display',
        'text-title',
        'text-body',
        'text-caption',
        'text-mono',
        'text-micro',
      ],
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return customTwMerge(clsx(inputs))
}
