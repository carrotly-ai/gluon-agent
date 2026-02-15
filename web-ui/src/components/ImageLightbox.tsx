import { Download, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

interface ImageLightboxProps {
  src: string
  alt: string
  children: React.ReactNode
}

export function ImageLightbox({ src, alt, children }: ImageLightboxProps) {
  const [open, setOpen] = useState(false)

  const close = useCallback(() => setOpen(false), [])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, close])

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
              onClick={(e) => e.stopPropagation()}
            />
            <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-3 text-white/80 text-[0.65rem]">
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
          document.body,
        )}
    </>
  )
}
