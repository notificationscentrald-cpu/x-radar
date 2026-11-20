// Golem tamagotchi: the pet's state and which animation the widget should show.
// Shared by widget.tsx (draws) and app_intents.tsx (buttons). Storage is a Scripting global.

declare const Storage: {
  get<T>(key: string): T | null
  set<T>(key: string, value: T): boolean
}

export type Clip = "idle" | "walk" | "knock" | "wipe" | "eat" | "sleep" | "sad" | "happy"
export type Action = "feed" | "pet" | "knock"

// frame count and loop length (seconds) of every pre-rendered clip in ./frames/<clip>/NN.jpg
export const CLIPS: Record<Clip, { n: number; dur: number }> = {
  idle: { n: 24, dur: 4 },
  walk: { n: 32, dur: 6.4 },
  knock: { n: 24, dur: 4 },
  wipe: { n: 24, dur: 4 },
  eat: { n: 18, dur: 3 },
  sleep: { n: 16, dur: 4 },
  sad: { n: 16, dur: 4 },
  happy: { n: 18, dur: 3 },
}

export type Golem = {
  food: number      // 0..100, 100 = full
  mood: number      // 0..100
  at: number        // when food/mood were last brought up to date (ms)
  last?: Action     // last button pressed
  lastAt?: number   // when (ms)
  born: number
  meals: number
}

const KEY = "golem.v1"
const HOUR = 3600e3
const FOOD_PER_HOUR = 100 / 14   // empty after ~14 hours without food
const MOOD_PER_HOUR = 100 / 10   // bored after ~10 hours without attention
const REACT_MS = 75e3            // how long a reaction to a button plays

const clamp = (x: number) => Math.max(0, Math.min(100, x))

export function load(now = Date.now()): Golem {
  const g = Storage.get<Golem>(KEY) ?? { food: 80, mood: 80, at: now, born: now, meals: 0 }
  const h = Math.max(0, (now - g.at) / HOUR)
  // he gets hungry a bit slower than a real pet: 0.75 of the nominal rate
  g.food = clamp(g.food - h * FOOD_PER_HOUR * 0.75)
  g.mood = clamp(g.mood - h * MOOD_PER_HOUR * 0.75)
  g.at = now
  return g
}

export function save(g: Golem) { Storage.set(KEY, g) }

export function act(kind: Action, now = Date.now()) {
  const g = load(now)
  if (kind === "feed") { g.food = clamp(g.food + 35); g.mood = clamp(g.mood + 5); g.meals++ }
  if (kind === "pet") g.mood = clamp(g.mood + 25)
  if (kind === "knock") g.mood = clamp(g.mood + 10)
  g.last = kind
  g.lastAt = now
  save(g)
}

// The day is cut into 10-minute slots; each slot plays one everyday scene.
const SLOT_MS = 10 * 60e3
const DAY_PLAN: Clip[] = ["idle", "walk", "knock", "wipe", "walk", "idle", "walk", "knock", "wipe", "idle", "walk", "walk"]

export function pick(g: Golem, now = Date.now()): { clip: Clip; until: number; line: string } {
  const since = g.lastAt ? now - g.lastAt : Infinity
  if (since < REACT_MS) {
    const clip: Clip = g.last === "feed" ? "eat" : g.last === "pet" ? "happy" : "knock"
    const line = g.last === "feed" ? "Смачний камінчик!" : g.last === "pet" ? "Ще! Ще!" : "Тук-тук у відповідь"
    return { clip, until: (g.lastAt as number) + REACT_MS, line }
  }
  // after you knock, he wipes the glass he just smudged
  if (g.last === "knock" && since < REACT_MS + SLOT_MS) {
    return { clip: "wipe", until: (g.lastAt as number) + REACT_MS + SLOT_MS, line: "Зараз протру скло" }
  }
  const hour = new Date(now).getHours()
  const nextSlot = (Math.floor(now / SLOT_MS) + 1) * SLOT_MS
  if (hour >= 23 || hour < 7) return { clip: "sleep", until: nextSlot, line: "Хррр... не буди" }
  if (g.food < 25) return { clip: "sad", until: nextSlot, line: "Я голодний..." }
  if (g.mood < 20) return { clip: "sad", until: nextSlot, line: "Мені сумно" }
  const clip = DAY_PLAN[Math.floor(now / SLOT_MS) % DAY_PLAN.length]
  const lines: Record<Clip, string> = {
    idle: "Чекаю тебе", walk: "Гуляю", knock: "Тук-тук! Ти там?", wipe: "Протираю скло",
    eat: "", sleep: "", sad: "", happy: "",
  }
  return { clip, until: nextSlot, line: lines[clip] }
}
