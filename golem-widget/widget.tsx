// Golem tamagotchi widget for the Scripting app.
// Plays a pre-rendered loop of the granite golem and shows three buttons that work right on the Home Screen.
import * as S from "scripting"
import { Button, HStack, Image, Path, Script, Spacer, Text, VStack, Widget, ZStack } from "scripting"
import { FeedGolem, KnockGolem, PetGolem } from "./app_intents"
import { CLIPS, Clip, load, pick, save } from "./golem_state"

const INK = "#1c1b18"
const PAPER = "#f5f3ee"
const AMBER = "#e8a33a"

function frame(clip: Clip, i: number) {
  return Path.join(Script.directory, "frames", clip, String(i).padStart(2, "0") + ".jpg")
}

function Scene({ clip }: { clip: Clip }) {
  const c = CLIPS[clip]
  const Frames = (S as any).AnimatedFrames
  const img = (i: number) => <Image filePath={frame(clip, i)} resizable scaleToFill />
  if (!Frames) return img(0)        // older app version: a still picture instead of a loop
  const kids = []
  for (let i = 0; i < c.n; i++) kids.push(img(i))
  return <Frames duration={c.dur}>{kids}</Frames>
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
    <Text font={17} frame={{ width: 34, height: 34 }} background={PAPER} clipShape="circle">{icon}</Text>
  </Button>
}

function View() {
  const now = Date.now()
  const g = load(now)
  save(g)
  const { clip, until, line } = pick(g, now)
  const small = Widget.family === "systemSmall"

  const view = <ZStack widgetBackground={PAPER} frame={{ maxWidth: "infinity", maxHeight: "infinity" }}>
    <Scene clip={clip} />
    <VStack frame={{ maxWidth: "infinity", maxHeight: "infinity" }} padding={small ? 8 : 10} alignment="leading">
      <HStack>
        <VStack alignment="leading" spacing={1} padding={{ horizontal: 6, vertical: 3 }} background={PAPER} clipShape={{ type: "rect", cornerRadius: 8 }}>
          {small ? null : <Text font={10} fontWeight="heavy" foregroundStyle={INK}>{line}</Text>}
          <Bar label="Їжа" value={g.food} color={AMBER} />
          <Bar label="Настрій" value={g.mood} color="#7aa84a" />
        </VStack>
        <Spacer />
      </HStack>
      <Spacer />
      <HStack spacing={small ? 6 : 8}>
        <Spacer />
        <Btn icon="🪨" intent={FeedGolem(undefined)} />
        <Btn icon="🤚" intent={PetGolem(undefined)} />
        <Btn icon="✊" intent={KnockGolem(undefined)} />
      </HStack>
    </VStack>
  </ZStack>

  Widget.present(view, { reloadPolicy: { policy: "after", date: new Date(Math.max(until, now + 60e3)) } })
}

View()
