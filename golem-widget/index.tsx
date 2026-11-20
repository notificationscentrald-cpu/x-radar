// Golem installer. Paste this into a new Scripting script named "Golem" and press Run once.
// It downloads the widget code and the golem frames, then puts them next to this file.
import { Path, Script, Widget, fetch } from "scripting"

declare const FileManager: any

const ZIP = "https://raw.githubusercontent.com/notificationscentrald-cpu/x-radar/claude/project-thread-qe6eq9/golem-widget/golem-widget.zip"

async function tell(title: string, message: string) {
  const D = (globalThis as any).Dialog
  if (D && D.alert) await D.alert({ title, message })
  else console.log(title + "\n" + message)
}

async function install() {
  const tmp = Path.join(FileManager.temporaryDirectory, "golem-install")
  const zip = tmp + ".zip"
  if (await FileManager.exists(tmp)) await FileManager.remove(tmp)
  const res = await fetch(ZIP)
  if (!res.ok) throw new Error("Не вдалося завантажити файли (код " + res.status + ")")
  await FileManager.writeAsData(zip, await res.data())
  await FileManager.createDirectory(tmp, true)
  await FileManager.unzip(zip, tmp)
  const names: string[] = await FileManager.readDirectory(tmp)
  let n = 0
  for (const name of names) {
    if (name.startsWith(".") || name.startsWith("__MACOSX")) continue
    const dest = Path.join(Script.directory, name)
    if (await FileManager.exists(dest)) await FileManager.remove(dest)
    await FileManager.rename(Path.join(tmp, name), dest)
    n++
  }
  await FileManager.remove(zip)
  Widget.reloadAll()
  return n
}

async function main() {
  try {
    const n = await install()
    await tell("Голем на місці", "Встановлено файлів і папок: " + n + ".\n\nТепер додай віджет: затисни порожнє місце на головному екрані → «+» → Scripting → середній розмір → «Додати». Потім затисни віджет → «Редагувати віджет» → Script: Golem.")
  } catch (e) {
    await tell("Щось пішло не так", String(e) + "\n\nПеревір інтернет і запусти ще раз.")
  }
  Script.exit()
}

main()
