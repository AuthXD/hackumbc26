import type { SpeakEvent } from "./types";

/**
 * Speaks coaching events. Tries the backend's ElevenLabs proxy first (when configured), and falls
 * back to the browser's speechSynthesis. Errors interrupt whatever is playing; other events queue.
 */
class Speaker {
  muted = false;
  useElevenLabs = false;
  private audio: HTMLAudioElement | null = null;
  private queue: SpeakEvent[] = [];
  private busy = false;

  say(ev: SpeakEvent) {
    if (this.muted) return;
    if (ev.priority === "error") {
      this.stop();
      this.queue = [ev];
    } else {
      this.queue.push(ev);
      if (this.queue.length > 3) this.queue.splice(0, this.queue.length - 3);
    }
    void this.pump();
  }

  stop() {
    this.queue = [];
    this.audio?.pause();
    this.audio = null;
    window.speechSynthesis?.cancel();
    this.busy = false;
  }

  private async pump() {
    if (this.busy) return;
    const ev = this.queue.shift();
    if (!ev) return;
    this.busy = true;
    try {
      const ok = this.useElevenLabs && (await this.playElevenLabs(ev.text));
      if (!ok) await this.playBrowser(ev.text);
    } finally {
      this.busy = false;
      void this.pump();
    }
  }

  private async playElevenLabs(text: string): Promise<boolean> {
    try {
      const res = await fetch("/api/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) return false;
      const url = URL.createObjectURL(await res.blob());
      const audio = new Audio(url);
      this.audio = audio;
      await new Promise<void>((resolve) => {
        audio.onended = audio.onerror = audio.onpause = () => resolve();
        audio.play().catch(() => resolve());
      });
      URL.revokeObjectURL(url);
      return true;
    } catch {
      return false;
    }
  }

  private playBrowser(text: string): Promise<void> {
    const synth = window.speechSynthesis;
    if (!synth) return Promise.resolve();
    return new Promise((resolve) => {
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 1.05;
      u.onend = u.onerror = () => resolve();
      synth.speak(u);
      // Some browsers never fire onend if speech is blocked; don't hang the queue.
      window.setTimeout(resolve, 15000);
    });
  }
}

export const speaker = new Speaker();
