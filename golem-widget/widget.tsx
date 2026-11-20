// Golem tamagotchi widget for the Scripting app.
// Plays a pre-rendered loop of the granite golem and shows three buttons that work right on the Home Screen.
import * as S from "scripting"
import { Button, HStack, Image, Path, Script, Spacer, Text, VStack, Widget, ZStack } from "scripting"
import { FeedGolem, KnockGolem, PetGolem } from "./app_intents"
import { Clip, load, pick, save } from "./golem_state"

declare const FileManager: any

const INK = "#1c1b18"
const PAPER = "#f5f3ee"
const AMBER = "#e8a33a"
const SCENE_W = 420, SCENE_H = 196   // size the frames were rendered at

type Meta = Record<Clip, { n: number; dur: number; r: number[][] }>
const META: Meta = JSON.parse(FileManager.readAsStringSync(Path.join(Script.directory, "frames_meta.json")))

const file = (clip: Clip, name: string) => Path.join(Script.directory, "frames", clip, name)

// Where the scene sits in the widget: scaled to fill, centred (same as scaleToFill on the room picture).
function sceneBox(W: number, H: number) {
  const k = Math.max(W / SCENE_W, H / SCENE_H)
  const w = SCENE_W * k, h = SCENE_H * k
  return { w, h, ox: (W - w) / 2, oy: (H - h) / 2 }
}

// The room: one still picture behind everything.
function Room({ clip, W, H }: { clip: Clip; W: number; H: number }) {
  return <Image filePath={file(clip, "bg.jpg")} resizable scaleToFill frame={{ width: W, height: H }} />
}

// The golem: a loop of small transparent pictures, each placed where it belongs in the scene.
// Frame rate comes from the widget's Parameter field: 60, 30 (default), 20 or 12.
// Frames are stored at 60 fps; a lower rate just skips frames and needs less widget memory.
function Golem({ clip, W, H }: { clip: Clip; W: number; H: number }) {
  const m = META[clip]
  const box = sceneBox(W, H)
  const want = Number(String(Widget.parameter ?? "").trim()) || 30
  const fps = Math.max(5, Math.min(60, want))
  const step = Math.max(1, Math.round(m.n / m.dur / fps))
  const one = (i: number) => {
    const [x, y, w, h] = m.r[i]
    return <ZStack frame={{ width: W, height: H, alignment: "topLeading" }}>
      <Image filePath={file(clip, String(i).padStart(3, "0") + ".png")} resizable
        frame={{ width: w * box.w, height: h * box.h }}
        offset={{ x: box.ox + x * box.w, y: box.oy + y * box.h }} />
    </ZStack>
  }
  const Frames = (S as any).AnimatedFrames
  if (!Frames) return one(0)        // older app version: a still golem instead of a loop
  const kids = []
  for (let i = 0; i < m.n; i += step) kids.push(one(i))
  return <Frames duration={m.dur}>{kids}</Frames>
}

function Bar({ label, value, color }: { label: string; value: number; color: string }) {
  const filled = Math.round(value / 20)
  return <HStack spacing={3}>
    <Text font={9} fontWeight="bold" foregroundStyle={INK}>{label}</Text>
    <Text font={9} foregroundStyle={color}>{"●".repeat(filled)}</Text>
    <Text font={9} foregroundStyle="#b9b4aa">{"●".repeat(5 - filled)}</Text>
  </HStack>
}

function Btn({ icon, intent }: { icon: string; intent: any }) {
  return <Button intent={intent} buttonStyle="plain">
    <Text font={17} frame={{ width: 34, height: 34 }} background={PAPER} clipShape={{ type: "capsule" }}>{icon}</Text>
  </Button>
}

function View() {
  const now = Date.now()
  const g = load(now)
  save(g)
  const { clip, until, line } = pick(g, now)
  const small = Widget.family === "systemSmall"

  const W = Widget.displaySize.width, H = Widget.displaySize.height
  const pad = small ? 8 : 10

  // back to front: room, the food/mood panel, the golem (so he walks in front of the panel), buttons
  const view = <ZStack widgetBackground={PAPER} frame={{ width: W, height: H }}>
    <Room clip={clip} W={W} H={H} />
    <VStack frame={{ width: W, height: H, alignment: "topLeading" }} padding={pad}>
      <VStack alignment="leading" spacing={1} padding={{ horizontal: 6, vertical: 3 }}>
        {small ? null : <Text font={10} fontWeight="heavy" foregroundStyle={INK}>{line}</Text>}
        <Bar label="Їжа" value={g.food} color={AMBER} />
        <Bar label="Настрій" value={g.mood} color="#7aa84a" />
      </VStack>
    </VStack>
    <Golem clip={clip} W={W} H={H} />
    <VStack frame={{ width: W, height: H, alignment: "bottomTrailing" }} padding={pad}>
      <HStack spacing={small ? 6 : 8}>
        <Btn icon="🪨" intent={FeedGolem(undefined)} />
        <Btn icon="🤚" intent={PetGolem(undefined)} />
        <Btn icon="✊" intent={KnockGolem(undefined)} />
      </HStack>
    </VStack>
  </ZStack>

  Widget.present(view, { reloadPolicy: { policy: "after", date: new Date(Math.max(until, now + 60e3)) } })
}

View()
