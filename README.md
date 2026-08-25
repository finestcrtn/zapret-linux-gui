# Zapret Linux GUI

Обход блокировок сайтов (DPI) для Linux. Вся настройка происходит автоматически.

## Установка

**Вариант 1 — из Release:**

1. Откройте [Releases](https://github.com/finestcrtn/zapret-linux-gui/releases)
2. Скачайте **`zapret-gui.flatpak`**
3. Установите: дважды щёлкните по скачанному файлу, или выполните:

   ```sh
   flatpak install --user ./zapret-gui.flatpak
   ```

4. Запустите **Zapret Control** из меню приложений.

**Вариант 2 — из исходников:**

```sh
git clone https://github.com/finestcrtn/zapret-linux-gui
cd zapret-linux-gui
./install.sh
```

> При первом запуске приложение само скачает zapret и один раз запросит пароль администратора — это нужно только один раз.

## Как пользоваться

- **Включить / выключить** — большая кнопка **Start / Stop**.
- **Добавить сайт** — вставьте ссылку (например `https://ya.ru`) в поле «Unblock a site» и нажмите **Add** — сайт и его ресурсы добавятся сами.
- **Стратегия** — если сайт открывается плохо, выберите другую стратегию в списке и нажмите **Apply**.
- **Обновления** — кнопка **Check for updates**.

Нужен Linux на systemd + пакет `nftables` или `iptables`. Всё остальное приложение приносит с собой.