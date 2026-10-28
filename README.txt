AI Assistant для STE Slicer
===========================

Плагин-ассистент с интеграцией LLM. Открывается как вспомогательное
окно внутри слайсера (QWebEngineView), а не как отдельная страница
в браузере.

Установка
---------
1. Распакуйте архив с плагином.
2. Переместите папку AIAssistant_Plugin в директорию плагинов STE Slicer:
   - Windows: %APPDATA%\cura\5.0\plugins\
   - Linux:   ~/.local/share/cura/plugins/
   - macOS:   ~/Library/Application Support/cura/plugins/
3. Запустите STE Slicer.
4. Меню Extensions -> AI Assistant -> Открыть чат.

Настройка LLM
-------------
Плагин НЕ содержит API-ключей. Ключ задаётся одним из способов:

Способ 1 — переменная окружения:
   STE_AI_API_KEY=sk-...
   (опционально STE_AI_URL, STE_AI_MODEL)

Способ 2 — файл конфигурации:
   ~/.config/ste-slicer/ai.json   (Linux/macOS)
   %APPDATA%\ste-slicer\ai.json   (Windows)

   Пример содержимого:
   {
       "api_key": "sk-...",
       "url":     "https://api.timeweb.ai/v1/chat/completions",
       "model":   "dashscope/qwen3.8-flash"
   }

Файл имеет приоритет над env-переменными.

Использование
-------------
- Опишите задачу или загрузите файл (STL/OBJ/3MF/DOCX/PDF/PPTX/TXT).
- Ассистент подберёт материал и параметры печати, загрузит модель,
  расставит её на столе и запустит слайсинг — через tool-calls к слайсеру.
- Кнопка "Открыть в браузере" в шапке окна — резервный режим.

Важно
-----
- При закрытии окна (X) оно скрывается, а не уничтожается: история
  чата сохраняется. Откройте снова через Extensions -> AI Assistant.
- Загруженные файлы хранятся в uploads/ и автоматически удаляются
  через 7 дней.
- pypdf / python-docx / python-pptx устанавливаются автоматически
  при первом запуске (если есть pip). При неудаче — установите вручную:
     pip install pypdf python-docx python-pptx