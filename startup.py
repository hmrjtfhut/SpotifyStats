import os
import sys
import winreg
from pathlib import Path

class AutoStartup:
    REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
    APP_NAME = "SpotifyStatsTracker"
    
    @staticmethod
    def enable_startup():
        """Enable app to start on Windows startup"""
        try:
            # Get the path to the main script or executable
            if getattr(sys, 'frozen', False):
                # Running as executable
                exe_path = sys.executable
            else:
                # Running as script - create a batch file in startup folder
                return AutoStartup.enable_startup_via_batch()
            
            # Write to registry
            try:
                key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, AutoStartup.REGISTRY_PATH)
                winreg.SetValueEx(key, AutoStartup.APP_NAME, 0, winreg.REG_SZ, exe_path)
                winreg.CloseKey(key)
                return True
            except Exception as e:
                print(f"Registry error: {e}")
                return AutoStartup.enable_startup_via_batch()
        
        except Exception as e:
            print(f"Error enabling startup: {e}")
            return False
    
    @staticmethod
    def enable_startup_via_batch():
        """Enable startup using batch file in startup folder"""
        try:
            startup_folder = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            startup_folder.mkdir(parents=True, exist_ok=True)
            
            # Create batch file
            batch_file = startup_folder / "SpotifyStatsTracker.bat"
            python_exe = sys.executable
            script_path = os.path.dirname(os.path.abspath(__file__))
            main_file = os.path.join(script_path, "main.py")
            
            batch_content = f"""@echo off
cd /d "{script_path}"
"{python_exe}" "{main_file}" --minimized
"""
            
            with open(batch_file, 'w') as f:
                f.write(batch_content)
            
            return True
        except Exception as e:
            print(f"Error creating batch file: {e}")
            return False
    
    @staticmethod
    def disable_startup():
        """Disable app from starting on Windows startup"""
        try:
            # Remove from registry
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AutoStartup.REGISTRY_PATH)
                winreg.DeleteValue(key, AutoStartup.APP_NAME)
                winreg.CloseKey(key)
            except:
                pass
            
            # Remove batch file
            startup_folder = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            batch_file = startup_folder / "SpotifyStatsTracker.bat"
            if batch_file.exists():
                batch_file.unlink()
            
            return True
        except Exception as e:
            print(f"Error disabling startup: {e}")
            return False
    
    @staticmethod
    def is_startup_enabled():
        """Check if startup is enabled"""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AutoStartup.REGISTRY_PATH)
            _, _ = winreg.QueryValueEx(key, AutoStartup.APP_NAME)
            winreg.CloseKey(key)
            return True
        except:
            # Check batch file
            startup_folder = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            batch_file = startup_folder / "SpotifyStatsTracker.bat"
            return batch_file.exists()
