from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QListWidget, QComboBox, QLineEdit, QWidget
from PyQt5 import QtWidgets


class ChooseFileDialog(QDialog):
    def __init__(self, files, parent=None):
        super().__init__(parent)
        self.listWidget = QListWidget()
        self.itemFileLinkedList = []
        for file in files:
            item = QtWidgets.QListWidgetItem(file)
            self.itemFileLinkedList.append((item, file))
            self.listWidget.addItem(item)
        buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        layout = QFormLayout(self)
        layout.addRow(self.listWidget)
        layout.addWidget(buttonBox)
        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)

    def getInputs(self):
        return self.getSelectedAttribute()

    def getSelectedFile(self):
        for item in self.listWidget.selectedItems():
            for item_, filename in self.itemFileLinkedList:
                if item_ is item:
                    return filename
