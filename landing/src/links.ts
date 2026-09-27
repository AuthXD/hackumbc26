function publicUrl(value: string | undefined): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  try {
    const url = new URL(trimmed);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    return url.toString();
  } catch {
    return null;
  }
}

export const demoVideoUrl = publicUrl(import.meta.env.VITE_DEMO_VIDEO_URL);
export const liveDemoUrl = publicUrl(import.meta.env.VITE_LIVE_DEMO_URL);
export const githubUrl = publicUrl(import.meta.env.VITE_GITHUB_URL);
