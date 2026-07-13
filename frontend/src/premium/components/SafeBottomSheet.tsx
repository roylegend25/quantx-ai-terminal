import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion, useReducedMotion, type PanInfo } from "framer-motion";
import { X } from "lucide-react";

type Props = {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
};

const DISMISS_OFFSET_PX = 120;
const DISMISS_VELOCITY = 800;

/** Same drag-to-dismiss/backdrop/scroll-lock mechanics as the existing
 * NotificationBell/MobileBottomNav "More" sheet (Classic), reimplemented
 * generically so Premium surfaces (appearance menu, filters, row detail on
 * phone) share one sheet component instead of each hand-rolling it. Honors
 * the safe-area bottom inset for iPhone home-indicator clearance. */
export default function SafeBottomSheet({ open, onClose, title, children }: Props) {
  const prefersReducedMotion = useReducedMotion();

  useEffect(() => {
    if (!open) return;
    document.body.classList.add("mobilenav-scroll-lock");
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.classList.remove("mobilenav-scroll-lock");
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  function handleDragEnd(_: unknown, info: PanInfo) {
    if (info.offset.y > DISMISS_OFFSET_PX || info.velocity.y > DISMISS_VELOCITY) onClose();
  }

  const spring = prefersReducedMotion ? { duration: 0.01 } : { type: "spring" as const, stiffness: 380, damping: 34 };

  return createPortal(
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="notif-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: prefersReducedMotion ? 0.01 : 0.18 }}
            onClick={onClose}
          />
          <motion.div
            className="notif-sheet qp-sheet"
            role="dialog"
            aria-modal
            aria-label={title || "Sheet"}
            initial={{ y: "100%" }}
            animate={{ y: 0 }}
            exit={{ y: "100%" }}
            transition={spring}
            drag="y"
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0, bottom: 0.5 }}
            onDragEnd={handleDragEnd}
          >
            <span className="notif-sheet-handle" aria-hidden="true" />
            {title && (
              <div className="notif-panel-head">
                <div className="notif-panel-title">
                  <b>{title}</b>
                </div>
                <button className="icon-btn notif-close" onClick={onClose} title="Close" aria-label="Close">
                  <X size={18} />
                </button>
              </div>
            )}
            <div className="notif-panel-body">{children}</div>
          </motion.div>
        </>
      )}
    </AnimatePresence>,
    document.body
  );
}
