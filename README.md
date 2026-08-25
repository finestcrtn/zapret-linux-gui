# Zapret Linux GUI

Обход блокировок сайтов (DPI) для Linux. Один раз скачивает и настраивает всё сама — включай и пользуйся.

## Установка

**Вариант 1 — из Release (проще всего):**

1. Открой [Releases](https://github.com/finestcrtn/zapret-linux-gui/releases)
2. Скачай **`zapret-gui.flatpak`**
3. Установи и запусти:

   ```sh
   flatpak install --user ./zapret-gui.flatpak
   ```

   В меню приложений появится **Zapret Control**.

**Вариант 2 — из исходников:**

```sh
git clone https://github.com/finestcrtn/zapret-linux-gui
cd zapret-linux-gui
./install.sh
```

> При первом запуске приложение само скачает zapret и **один раз** попросит пароль администратора — это нужно только один раз.

## Как пользоваться

- **Включить / выключить** — большая кнопка **Start / Stop**.
- **Добавить сайт** — вставь ссылку (например `https://ya.ru`) в поле «Unblock a site» и нажми **Add** — сайт и его ресурсы добавятся сами.
- **Стратегия** — если сайт открывается плохо, выбери другую стратегию в списке и нажми **Apply**.
- **Обновления** — кнопка **Check for updates**.

Нужен Linux на systemd + пакет `nftables` или `iptables`. Всё остальное приложение приносит с собой.