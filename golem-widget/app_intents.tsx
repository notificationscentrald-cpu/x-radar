// Buttons on the widget. Each one updates the golem and redraws the widget at once.
import { AppIntentManager, AppIntentProtocol, Widget } from "scripting"
import { act } from "./golem_state"

export const FeedGolem = AppIntentManager.register({
  name: "FeedGolem",
  protocol: AppIntentProtocol.AppIntent,
  perform: async (_: undefined) => { act("feed"); Widget.reloadAll() },
})

export const PetGolem = AppIntentManager.register({
  name: "PetGolem",
  protocol: AppIntentProtocol.AppIntent,
  perform: async (_: undefined) => { act("pet"); Widget.reloadAll() },
})

export const KnockGolem = AppIntentManager.register({
  name: "KnockGolem",
  protocol: AppIntentProtocol.AppIntent,
  perform: async (_: undefined) => { act("knock"); Widget.reloadAll() },
})
