import Opactiy
import shlex

class Interface:
    @staticmethod
    def run():
        #os.system('cls')
        print("Thank you for using my Opaque CLI client!\n"
              "In case you feel grateful and want to donate something, here is my ETH-address: \n{}\n\n".format(
                "0xD413A626F9dd91D7c2d084a1d7b3d8f2B6fcA085"))

        print("Let's start!\n")
        print("Your Opaque handle:")
        handle = input()
        if len(handle) != 128:
            print("This handle isn't 128 characters long, please make sure you use the correct handle!")
        else:
            acc = Opactiy.Opacity(handle)
            #os.system('cls')
            print("Thank you for logging. Feel free to interact now with the opque cli.\n"
                  "If you need help just type 'help' or '?'")
            while True:
                try:
                    action = input()
                    action = shlex.split(action)
                    if action[0] == "help" or action[0] == "?":
                        Interface.printHelp()
                    elif action[0] == "download":
                        if len(action[1]) == 128 and len(action[2]) > 0:
                            acc.Download(action[1], action[2])
                    elif action[0] == "upload":
                        if len(action) != 3:
                            print("To upload files do it in the following format!\n{} {} {}"
                                  .format(r"upload", r'"C:\pathtofile\orfolder"', r'"/"'))
                        else:
                            acc.upload(action[1], action[2])
                            print("-------")
                    elif action[0] == "delete":
                        acc.delete(action[1], action[2])
                    elif action[0] == "createFolder":
                        acc.createFolder(action[1])
                    elif action[0] == "dir":
                        if len(action) == 2:
                            acc.getFolderData(action[1])
                            acc.showFiles()
                        else:
                            print("Please provide the folderpath!")
                    elif action[0] == "move":
                        acc.move(action[1], action[2], action[3])
                    else:
                        print("unrecognized command")
                except Exception as e:
                    print("Error: {}".format(e))


    @staticmethod
    def printHelp():
        print('\nUsage:\n'
              'Important fact about opacity\'s pathing system:'
              '\n\tThe main folder corresponds to the path "/", so the path of a subdirectory would be "/subdir".\n\n'
              'upload <path to file/folder> <directory in opacity to save to>\n'
              'download <file handle> <saving path>\n'
              'delete <directory of file/folder> <file/folder handle>\n'
              'move <folder path in opacity> <file or folder handle> <move to folder path in opacity>\n'
              'createFolder <path of folder>\n'
              'dir <folder path in opacity>\n')

if __name__ == "__main__":
    Interface.run()