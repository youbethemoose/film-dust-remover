local LrApplication     = import 'LrApplication'
local LrBinding         = import 'LrBinding'
local LrDialogs         = import 'LrDialogs'
local LrExportSession   = import 'LrExportSession'
local LrFileUtils       = import 'LrFileUtils'
local LrFunctionContext = import 'LrFunctionContext'
local LrPathUtils       = import 'LrPathUtils'
local LrProgressScope   = import 'LrProgressScope'
local LrTasks           = import 'LrTasks'
local LrView            = import 'LrView'

-- ─── Utilities ────────────────────────────────────────────────────────────────

local function findPython()
    for _, cmd in ipairs({ 'python3', 'python' }) do
        local h = io.popen(cmd .. ' --version 2>&1')
        if h then
            local out = h:read('*all') ; h:close()
            if out and out:find('Python %d') then return cmd end
        end
    end
    return nil
end

local function checkDeps(python)
    local h   = io.popen(python .. ' -c "import cv2, numpy" 2>&1')
    local out = h:read('*all') ; h:close()
    return not (out and out:find('ModuleNotFoundError'))
end

-- Safely quote a path for POSIX shell (single-quote wrapping)
local function q(path)
    return "'" .. path:gsub("'", "'\\''") .. "'"
end

-- ─── Dialog ───────────────────────────────────────────────────────────────────

local function showDialog(photoCount)
    local settings = nil

    LrFunctionContext.callWithContext('filmDustDialog', function(context)
        local props = LrBinding.makePropertyTable(context)
        props.sensitivity = 65
        props.suffix      = '_clean'

        local f    = LrView.osFactory()
        local bind = LrView.bind
        local LBL  = LrView.share 'lbl'

        local content = f:column {
            bind_to_object = props,
            spacing        = f:control_spacing(),

            f:row {
                f:static_text {
                    title = ('Film Dust Remover  —  %d photo%s selected')
                            :format(photoCount, photoCount == 1 and '' or 's'),
                    font  = '<system/bold>',
                },
            },

            f:separator { fill_horizontal = 1 },

            -- Sensitivity
            f:row {
                spacing = f:label_spacing(),
                f:static_text {
                    title = 'Detection Sensitivity:', alignment = 'right', width = LBL,
                },
                f:slider {
                    value = bind 'sensitivity', min = 1, max = 100,
                    integral = true, width = 220,
                },
                f:static_text {
                    title = bind {
                        key       = 'sensitivity',
                        transform = function(v)
                            return ('%3d'):format(math.floor(v or 65))
                        end,
                    },
                    width = 28, alignment = 'center',
                },
            },
            f:row {
                f:static_text { title = '', width = LBL },
                f:static_text {
                    title = '1 = obvious spots only     50 = balanced     100 = all dust + hairs',
                    font  = '<system/small>',
                },
            },

            f:spacer { height = 6 },

            -- Output suffix
            f:row {
                spacing = f:label_spacing(),
                f:static_text {
                    title = 'Output Suffix:', alignment = 'right', width = LBL,
                },
                f:edit_field { value = bind 'suffix', width = 100 },
                f:static_text {
                    title = '  added before the file extension',
                    font  = '<system/small>',
                },
            },

            f:separator { fill_horizontal = 1 },

            f:static_text {
                title = 'Detects and removes dust spots from film scans. Works with ARW,\n'
                      ..'DNG, TIFF, JPEG and all Lightroom formats. Originals are never\n'
                      ..'modified. Cleaned TIFFs are saved alongside and imported.',
                font  = '<system/small>',
            },
        }

        local result = LrDialogs.presentModalDialog {
            title      = 'Film Dust Remover',
            contents   = content,
            actionVerb = 'Remove Dust',
        }

        if result == 'ok' then
            settings = {
                sensitivity = math.floor(props.sensitivity or 65),
                suffix      = props.suffix or '_clean',
            }
        end
    end)

    return settings
end

-- ─── Entry Point ──────────────────────────────────────────────────────────────

LrTasks.startAsyncTask(function()
    local catalog = LrApplication.activeCatalog()
    local photos  = catalog:getTargetPhotos()

    if not photos or #photos == 0 then
        LrDialogs.message('Film Dust Remover',
            'Please select at least one photo in the Library first.', 'info')
        return
    end

    -- Verify Python
    local python = findPython()
    if not python then
        LrDialogs.message('Film Dust Remover — Python Not Found',
            'Python 3 is required.\n\n'
          ..'1. Install Python from python.org\n'
          ..'2. Run setup.sh from the plugin folder\n'
          ..'3. Restart Lightroom', 'critical')
        return
    end

    if not checkDeps(python) then
        LrDialogs.message('Film Dust Remover — Missing Dependencies',
            'OpenCV and NumPy are not installed.\n\n'
          ..'Run setup.sh from the plugin folder, or open Terminal and run:\n\n'
          ..'  pip3 install opencv-python-headless numpy', 'critical')
        return
    end

    local settings = showDialog(#photos)
    if not settings then return end  -- user cancelled

    local scriptPath = LrPathUtils.child(_PLUGIN.path, 'dust_remover.py')

    -- Temp directory for Lightroom-rendered TIFFs (handles ARW, DNG, RAW, etc.)
    local tmpDir = '/tmp/FilmDustRemover_' .. tostring(os.time())
    io.popen('mkdir -p ' .. q(tmpDir)):read('*all')

    -- ── Phase 1: Render all photos to 16-bit TIFFs via Lightroom ─────────────
    -- This handles ANY format Lightroom supports (ARW, DNG, TIFF, JPEG, etc.)
    local rendered = {}  -- { photo, tmpPath }

    LrFunctionContext.callWithContext('dustRender', function(context)
        local progress = LrProgressScope {
            title           = 'Film Dust Remover — Rendering Photos',
            functionContext = context,
        }

        local exportSession = LrExportSession {
            photosToExport = photos,
            exportSettings = {
                LR_export_destinationType        = 'specificFolder',
                LR_export_destinationPathPrefix  = tmpDir,
                LR_export_useSubfolder           = false,
                LR_format                        = 'TIFF',
                LR_tiff_preserveTransparency     = false,
                LR_16bit_tiff                    = true,
                LR_outputSharpeningOn            = false,
                LR_size_doNotEnlarge             = true,
                LR_reimportExportedPhoto         = false,
                LR_embeddedMetadataOption        = 'all',
                LR_minimizeEmbeddedMetadata      = false,
                LR_includeVideoFiles             = false,
            },
        }

        local i = 0
        for _, rendition in exportSession:renditions() do
            if progress:isCanceled() then break end
            i = i + 1
            local name = rendition.photo:getFormattedMetadata('fileName') or ('Photo ' .. i)
            progress:setCaption('Rendering: ' .. name)
            progress:setPortionComplete(i - 1, #photos)

            local success, pathOrMsg = rendition:waitForRender()
            if success then
                table.insert(rendered, { photo = rendition.photo, tmpPath = pathOrMsg })
            end
        end

        progress:done()
    end)

    if #rendered == 0 then
        io.popen('rm -rf ' .. q(tmpDir)):read('*all')
        LrDialogs.message('Film Dust Remover', 'No photos could be rendered.', 'critical')
        return
    end

    -- ── Phase 2: Run Python dust removal on each rendered TIFF ───────────────
    local successes = {}
    local failures  = {}

    LrFunctionContext.callWithContext('dustProcess', function(context)
        local progress = LrProgressScope {
            title           = 'Film Dust Remover — Removing Dust',
            functionContext = context,
        }

        for i, item in ipairs(rendered) do
            if progress:isCanceled() then break end

            local photo   = item.photo
            local tmpTiff = item.tmpPath
            local name    = photo:getFormattedMetadata('fileName') or ('Photo ' .. i)

            progress:setCaption('Cleaning: ' .. name)
            progress:setPortionComplete(i - 1, #rendered)

            -- Output goes alongside the original file (not in temp dir)
            local origPath = photo:getRawMetadata('path')
            local dir      = LrPathUtils.parent(origPath)
            local base     = LrPathUtils.removeExtension(LrPathUtils.leafName(origPath))
            local out      = LrPathUtils.child(dir, base .. settings.suffix .. '.tif')

            -- Avoid overwriting an existing file
            local counter = 1
            while LrFileUtils.exists(out) do
                out = LrPathUtils.child(dir,
                    base .. settings.suffix .. '_' .. counter .. '.tif')
                counter = counter + 1
            end

            local cmd = python
                .. ' ' .. q(scriptPath)
                .. ' ' .. q(tmpTiff)
                .. ' ' .. q(out)
                .. ' --sensitivity ' .. tostring(settings.sensitivity)
                .. ' 2>&1'

            local h      = io.popen(cmd)
            local output = h:read('*all')
            h:close()

            -- Clean up individual temp TIFF immediately
            LrFileUtils.delete(tmpTiff)

            if LrFileUtils.exists(out) then
                table.insert(successes, { path = out, near = photo })
            else
                table.insert(failures, { name = name, reason = output or 'unknown error' })
            end
        end

        progress:done()
    end)

    -- Clean up temp directory
    io.popen('rm -rf ' .. q(tmpDir)):read('*all')

    -- ── Phase 3: Import cleaned TIFFs into catalog ────────────────────────────
    if #successes > 0 then
        catalog:withWriteAccessDo('Import Dust-Removed Photos', function()
            for _, s in ipairs(successes) do
                catalog:addPhoto(s.path)
            end
        end)
    end

    -- Summary
    local lines = {}
    table.insert(lines, ('%d photo%s cleaned and imported into your catalog.'):format(
        #successes, #successes == 1 and '' or 's'))

    if #failures > 0 then
        table.insert(lines, '')
        table.insert(lines, ('%d failed:'):format(#failures))
        for _, f in ipairs(failures) do
            table.insert(lines, '  \xe2\x80\xa2 ' .. f.name .. ' \xe2\x80\x94 ' .. f.reason)
        end
    end

    LrDialogs.message('Film Dust Remover — Complete',
        table.concat(lines, '\n'), 'info')
end)
