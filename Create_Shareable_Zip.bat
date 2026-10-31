@echo off
chcp 65001 >nul
title ساخت پکیج اشتراک‌گذاری دانلودر یوتیوب و اسپاتیفای
echo.
echo ====================================================================
echo   YouTube ^& Spotify Downloader - Standalone Packaging Tool
echo ====================================================================
echo.
echo [1/2] در حال بیلد آخرین نسخه مستقل...
dotnet publish src/desktop/app/YouTubeDownloaderApp.csproj -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -o build_out
copy /Y build_out\YouTube_Downloader.exe YouTube_Downloader.exe >nul
echo.
echo [2/2] در حال بسته‌بندی پکیج پرتابل در پوشه dist...
runtime\python.exe tools\build_standalone.py
echo.
echo ====================================================================
echo فایل زیپ آماده ارسال در پوشه dist ذخیره شد.
echo ====================================================================
pause
