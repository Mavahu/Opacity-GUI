from kivy.config import Config

Config.set('graphics', 'width', '1524')
Config.set('graphics', 'height', '720')
Config.set('input', 'mouse', 'mouse,multitouch_on_demand')
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.popup import Popup
from kivy.uix.button import Button
from kivy.core.window import Window
from kivy.uix.screenmanager import Screen
from kivy.properties import ObjectProperty, StringProperty, NumericProperty, ListProperty
from kivy.clock import Clock
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivy.uix.boxlayout import BoxLayout
from kivymd.uix.screen import MDScreen

import Opactiy
import keyring
from threading import Thread
import pyperclip
import posixpath
import datetime as dt
import time


class FolderItem(MDBoxLayout):
    name = StringProperty()
    handle = StringProperty()


class FileItem(BoxLayout):
    name = StringProperty()
    handle = StringProperty()
    created_date = StringProperty()
    timestamp = NumericProperty(0)


class DownloadDialog(FloatLayout):
    download = ObjectProperty(None)
    cancel = ObjectProperty(None)
    handles = ListProperty()


class UploadDialog(FloatLayout):
    upload = ObjectProperty(None)
    cancel = ObjectProperty(None)


class DeletePopup(FloatLayout):
    delete = ObjectProperty(None)
    cancel = ObjectProperty(None)


class PathButton(Button):
    text = StringProperty("")
    depth = NumericProperty(0)


class NewFolderPopup(FloatLayout):
    save = ObjectProperty(None)
    cancel = ObjectProperty(None)


class RenamePopup(FloatLayout):
    rename = ObjectProperty(None)
    cancel = ObjectProperty(None)
    oldname = StringProperty("")
    handle = StringProperty("")


class HeaderList(MDBoxLayout):
    pass


class UIWidget(Screen):
    recycle_view = ObjectProperty(None)
    scroller = ObjectProperty(None)
    path_visualizer = ObjectProperty(None)
    handle = StringProperty(None)
    account = ObjectProperty(None)
    current_path = StringProperty("/")
    output = ListProperty([])

    def __init__(self, **kwargs):
        super(UIWidget, self).__init__(**kwargs)
        Window.bind(on_dropfile=self._on_file_drop)
        self.ascending = False
        self.path_depth = 0
        Clock.schedule_once(self.checkForHandle, 0.25)

    def _on_file_drop(self, window, file_or_folder):
        self.account._queue.put({"action": "upload",
                                 "information": {
                                     "file_path": file_or_folder.decode("utf-8"),
                                     "opacity_path": self.current_path
                                 }})

    def multiple_delete(self):
        items = [item for item in self.scroller.children if item.checkbox.active]
        for item in items:
            self.scroller.remove_widget(item)
            self.account._queue.put({"action": "delete",
                                     "information": {
                                         "handle": item.handle,
                                         "opacity_path": self.current_path
                                     }})

    def checkForHandle(self, _):
        handle_check = keyring.get_password("Opacity", "handle")
        if keyring.get_password("Opacity", "handle") is None:
            self.openHandlePopup()
        else:
            self.handle = handle_check
            # print("savedhandle: {}".format(self.handle))
            self.loadAccount()

    def openHandlePopup(self):
        pops = PopupHandle()
        pops.entered_handle.bind(text=self.setter('handle'))
        pops.bind(on_dismiss=self.setHandle)
        pops.open()

    def setHandle(self, _):
        keyring.set_password("Opacity", "handle", self.handle)
        # print("newhandle: {}".format(self.handle))
        self.loadAccount()

    def resethandle(self):
        keyring.delete_password("Opacity", "handle")
        Clock.schedule_once(self.checkForHandle, 1)

    def loadAccount(self):
        self.account = Opactiy.Opacity(self.handle)
        # self.account.output = self.output
        Clock.schedule_once(lambda dt: self.load_path_content(), 0.1)
        #self.load_path_content()

    def load_path_content(self, _=None):
        # self.scroller.bind(minimum_height=self.scroller.setter('height'))
        start = time.time()
        self.account.getFolderData(self.current_path)
        print("{}".format(time.time()-start))
        start = time.time()
        account_metadata = self.account._metaData
        #self.scroller.clear_widgets()
        # for folder in account_metadata.folders:
        #     folderitem = FolderItem(name=folder.name, handle=folder.handle)
        #     self.scroller.add_widget(folderitem)
        for file in account_metadata.files:
            self.recycle_view.data.append(
                {"name": file.name,
                 "handle": file.versions[0].handle,
                 "timestamp": file.created,
                 "created_date": dt.datetime.utcfromtimestamp(file.created/1000.0).strftime("%d/%m/%Y")
                }
            )
        self.reset_sorts()
        print("{}".format(time.time()-start))
        # print(self.current_path)
        # print("")

    def update_path(self, newpath):
        self.current_path = posixpath.join(self.current_path, newpath)
        self.path_depth += 1
        copy_of_path_depth = self.path_depth
        # Clock.schedule_once(lambda  dt: self.update_2123(newpath, copy_of_path_depth),0.25)
        self.load_path_content()
        self.path_visualizer.add_widget(PathButton(text=newpath, depth=copy_of_path_depth))

    def back_to_path(self, button_id):
        # print(button_id)
        slashes = self.current_path.count("/")
        if button_id < slashes:
            if button_id == 0:
                self.current_path = "/"
                self.path_depth = 0
                for button in self.path_visualizer.children[::-1][1:]:
                    self.path_visualizer.remove_widget(button)
            else:
                self.path_depth = button_id
                path = self.current_path.split("/")
                self.current_path = ""
                for i in range(button_id):
                    self.current_path += "/" + path[i + 1]
                for button in self.path_visualizer.children[::-1][button_id + 1:]:
                    self.path_visualizer.remove_widget(button)
            self.load_path_content()
        # print("finished")

    def multiple_download(self):
        # print("multiple_download")
        items = [item for item in self.scroller.children if item.checkbox.active]
        if len(items) == 0:
            return
        for item in items:
            item.checkbox.active = False
        handles = [{"handle": item.handle, "name": item.name} for item in items]
        content = DownloadDialog(download=self.initiate_download, cancel=self.dismiss_download_popup, handles=handles)
        self._download_popup = Popup(title="Choose saving location", content=content, size_hint=(0.9, 0.9))
        self._download_popup.open()

    def show_download_dialog(self, handle, name):
        content = DownloadDialog(download=self.initiate_download, cancel=self.dismiss_download_popup,
                                 handles=[{"handle": handle, "name": name}])
        self._download_popup = Popup(title="Choose saving location", content=content, size_hint=(0.9, 0.9))
        self._download_popup.open()

    def initiate_download(self, path, handles):
        t = Thread(target=self.download_handles, args=(path, handles, self.current_path))
        t.daemon = True
        t.start()
        self.dismiss_download_popup()

    def download_handles(self, path, handles, current_path):
        for handle in handles:
            self.account.Download_GUI(item=handle, folderPath=current_path, pathToSave=path)

    def dismiss_download_popup(self):
        self._download_popup.dismiss()

    def show_upload(self):
        content = UploadDialog(upload=self.upload_files, cancel=self.dismiss_upload_popup)
        self._upload_popup = Popup(title="Select files to upload", content=content,
                                   size_hint=(0.9, 0.9))
        self._upload_popup.open()

    def upload_files(self, directory, files):
        files = [file for file in files if file != directory]
        for file in files:
            self.account._queue.put({"action": "upload",
                                     "information": {
                                         "file_path": file,
                                         "opacity_path": self.current_path
                                     }})
        self.dismiss_upload_popup()

    def dismiss_upload_popup(self):
        self._upload_popup.dismiss()

    def show_create_folder(self):
        content = NewFolderPopup(save=self.create_folder, cancel=self.dismiss_create_folder)
        self._create_folder_popup = Popup(title="Create Folder", content=content,
                                          size_hint=(None, None), height=130, width=350)
        self._create_folder_popup.open()

    def create_folder(self, name):
        folder = self.account.createFolder(posixpath.join(self.current_path, name))
        self.scroller.add_widget(FolderItem(name=folder.name, handle=folder.handle))
        self.dismiss_create_folder()

    def dismiss_create_folder(self):
        self._create_folder_popup.dismiss()

    def show_delete_popup(self, handle):
        content = DeletePopup(delete=lambda: self.delete_handle(handle), cancel=self.dismiss_delete_popup)
        self._delete_popup = Popup(title="Confirm deletion:", content=content, size_hint=(None, None),
                                   height=95, width=350)
        self._delete_popup.open()

    def delete_handle(self, handle):
        item = [item for item in self.scroller.children if item.handle == handle][0]
        self.scroller.remove_widget(item)
        self.account._queue.put({"action": "delete",
                                 "information": {
                                     "handle": handle,
                                     "opacity_path": self.current_path
                                 }})
        self.dismiss_delete_popup()

    def dismiss_delete_popup(self):
        self._delete_popup.dismiss()

    def show_rename_popup(self, handle, name):
        content = RenamePopup(rename=self.rename_item, cancel=self.dismiss_rename_popup,
                              oldname=posixpath.splitext(name)[0], handle=handle)
        self._rename_popup = Popup(title="Specify the name", content=content, size_hint=(None, None),
                                   height=130, width=350)
        self._rename_popup.open()

    def rename_item(self, new_name, old_name, handle):
        if new_name != old_name:
            # call rename function
            for item in self.scroller.children:
                if item.handle == handle:
                    item.name = new_name + posixpath.splitext(item.name)[1]
            # print("Renaming: file:{} handle: {}".format(new_name, handle))
            self.account.rename(self.current_path, handle, old_name, new_name)
        self.dismiss_rename_popup()

    def dismiss_rename_popup(self):
        self._rename_popup.dismiss()

    def copy_sharelink(self, handle):
        popup = Popup(title="Link was copied to the clipboard", width=240, height=50, size_hint=(None, None))
        popup.bind(on_touch_down=popup.dismiss)
        popup.open()
        link = "https://opacity.io/share#handle=" + handle
        pyperclip.copy(link)
        # print(link)

    def sort_items(self):
        '''
        sort folders
            1. get all folders/files
            2. sort them
            3. put them back reversed
        '''

        folders = []
        files = []
        for item in self.scroller.children[::-1]:
            if type(item).__name__ == "FileItem":
                files.append(item)
            elif type(item).__name__ == "FolderItem":
                folders.append(item)
            else:
                raise TypeError("Strange type encountered")

        # sort files
        folders = sorted(folders, key=lambda folder: (folder.name.casefold(), folder.name))
        files = sorted(files, key=lambda file: (file.name.casefold(), file.name))

        self.scroller.clear_widgets()
        self.ascending = not self.ascending
        if not self.ascending:
            print("sorted descending")
            self.header.name_sort.icon = "menu-up"
            folders = folders[::-1]
            files = files[::-1]
        else:
            print("sorted ascending")
            self.header.name_sort.icon = "menu-down"
        for folder in folders:
            self.scroller.add_widget(folder)
        for file in files:
            self.scroller.add_widget(file)

    def reset_sorts(self):
        self.ascending = False
        self.header.name_sort.icon = ""
        self.header.checkbox.active = False

    def change_all_checkboxes(self, *args):
        if args[1] == "down":  # checked
            for item in self.scroller.children:
                item.checkbox.active = True
        elif args[1] == "normal":  # unchecked
            for item in self.scroller.children:
                item.checkbox.active = False
        else:
            print("checkbox has a new value")

    def move_files(self):
        if self.move_button.text == "Move":
            items_to_move = []
            for item in self.scroller.children:
                if item.checkbox.active:
                    items_to_move.append({"handle": item.handle, "name": item.name})
                    item.checkbox.active = False
            if len(items_to_move) == 0:
                print("No item was selected")
                return

            self.items_to_move = dict()
            self.items_to_move["from"] = self.current_path
            self.items_to_move["items"] = items_to_move
            self.move_button.text = "Drop"
        elif self.move_button.text == "Drop":
            print("moving files")
            self.items_to_move["to"] = self.current_path
            self.move_button.text = "Move"
            for item in self.items_to_move["items"]:
                self.account._queue.put({"action": "move",
                                         "information": {
                                             "from_folder": self.items_to_move["from"],
                                             "object": item,
                                             "to_folder": self.items_to_move["to"]
                                         }})

class PopupHandle(Popup):
    entered_handle = ObjectProperty(None)
    error_label = ObjectProperty(None)

    def dismiss(self, *args, **kwargs):
        if len(self.entered_handle.text) != 128:
            self.error_label.text = "The account handle is incorrect!\nPlease make sure you use a 128 character long account handle."
            return True
        # print(self.entered_handle.text)
        super(PopupHandle, self).dismiss(**kwargs)


class OpacityGUIApp(MDApp):
    def build(self):
        temp = UIWidget()
        return temp


if __name__ == '__main__':
    t = OpacityGUIApp()
    t.run()
