#!/usr/bin/env bash
# Установка Zapret Linux GUI.
set -e

if ! command -v flatpak >/dev/null 2>&1; then
    echo "Не найден flatpak. Сначала установи его (см. документацию своего дистрибутива)." >&2
    exit 1
fi

if [ ! -f ./zapret-gui.flatpak ]; then
    echo "Файл zapret-gui.flatpak не найден — собираю из исходников..."
    ./build.sh
fi

flatpak install --user -y ./zapret-gui.flatpak

echo
echo "Готово. Запусти 'Zapret Control' из меню приложений:"
echo "  flatpak run io.github.zapretgui.ZapretGui"
echo
echo "Первый запуск скачает zapret и один раз спросит пароль — это нормально."