import { useState } from "react";

/**
 * RAUNDO brand mark. The owner supplies the original file as
 * `admin-web/public/logo.png` and we render it unchanged. Until the file is
 * present (or if it fails to load) we fall back to the letter mark so the
 * panel never shows a broken image.
 */
export function BrandLogo({ size = 30 }: { size?: number }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span className="brand-dot" aria-hidden>
        R
      </span>
    );
  }
  return (
    <img
      className="brand-logo"
      src="/logo.png"
      alt="RAUNDO"
      width={size}
      height={size}
      onError={() => setFailed(true)}
    />
  );
}
