import { Download, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

interface ImageLightboxProps {
  src: string
  alt: string
  children: React.ReactNode
}

/** Vertical distance (px) a downward swipe must travel to dismiss the lightbox. */
const SWIPE_DISMISS_THRESHOLD = 80

export function ImageLightbox({ src, alt, children }: ImageLightboxProps) {
  const [open, setOpen] = useState(false)
  // Live vertical offset while a downward swipe is in progress (px).
  const [dragY, setDragY] = useState(0)
  const touchStartY = useRef<number | null>(null)

  const close = useCallback(() => {
    setOpen(false)
    setDragY(0)
    touchStartY.current = null
  }, [])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, close])

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    touchStartY.current = e.touches[0]?.clientY ?? null
  }, [])

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (touchStartY.current === null) return
    const delta = (e.touches[0]?.clientY ?? 0) - touchStartY.current
    // Only follow downward swipes; ignore upward drift.
    setDragY(delta > 0 ? delta : 0)
  }, [])

  const onTouchEnd = useCallback(() => {
    if (dragY >= SWIPE_DISMISS_THRESHOLD) {
      close()
    } else {
      // Snap back if the swipe didn't cross the threshold.
      setDragY(0)
      touchStartY.current = null
    }
  }, [dragY, close])

  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className="contents">
        {children}
      </button>
      {open &&
        createPortal(
          <div
            className="lightbox-overlay fixed inset-0 z-[200] bg-black/90 flex items-center justify-center p-4"
            onClick={close}
            onTouchStart={onTouchStart}
            onTouchMove={onTouchMove}
            onTouchEnd={onTouchEnd}
          >
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                close()
              }}
              className="lightbox-close absolute top-4 right-4 p-2 text-white/70 hover:text-white transition-colors z-10"
              aria-label="Close"
            >
              <X className="w-6 h-6" />
            </button>
            <img
              src={src}
              alt={alt}
              className="max-w-full max-h-full object-contain rounded"
              style={
                dragY > 0
                  ? {
                      transform: `translateY(${dragY}px)`,
                      opacity: Math.max(0.4, 1 - dragY / 400),
                    }
                  : undefined
              }
              onClick={(e) => e.stopPropagation()}
            />
            <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-3 text-white/80 text-caption">
              <span className="truncate max-w-[200px]">{alt}</span>
              <a
                href={src}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 hover:text-white transition-colors shrink-0"
                onClick={(e) => e.stopPropagation()}
              >
                <Download className="w-3 h-3" />
                Open
              </a>
            </div>
          </div>,
          document.body
        )}
    </>
  )
}
